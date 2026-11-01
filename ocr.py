from __future__ import annotations

import io
import logging
import os
import re
import sys
import threading
from statistics import median
from typing import Any


# =========================================================
# ENVIRONMENT SETUP
# (must run BEFORE importing paddle / protobuf
#  because those libraries read these env vars at import time)
# =========================================================

if "APP_PATH" in os.environ:
    app_path = os.path.abspath(os.environ["APP_PATH"])

    if os.getcwd() != app_path:
        os.chdir(app_path)

    if app_path not in sys.path:
        sys.path.append(app_path)


# Paddle on Windows can crash with oneDNN/MKLDNN or thread oversubscription.
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_use_onednn", "0")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
# Tesseract 4 asks OpenMP for several threads itself (OMP_NUM_THREADS does
# not stop it). Parallel tesseract processes on a small CPU share then spin
# against each other; the Tesseract docs recommend this limit for that case.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
# PaddleOCR 2.x + mixed protobuf installs need pure-Python protobuf.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

# Optional: move temp files to another drive (e.g. when C: is full) by
# setting OCR_TMP_DIR. Never point it inside the app folder — the Functions
# host watches that folder and restarts on file changes, aborting requests.
_OCR_TMP_DIR = os.environ.get("OCR_TMP_DIR")
if _OCR_TMP_DIR:
    os.makedirs(_OCR_TMP_DIR, exist_ok=True)
    os.environ["TMP"] = _OCR_TMP_DIR
    os.environ["TEMP"] = _OCR_TMP_DIR
os.environ.setdefault(
    "MODELSCOPE_CACHE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".modelscope"),
)


import numpy as np
import pypdfium2
import pytesseract

from PIL import Image


_DEFAULT_TESSERACT_HOME = r"C:\Program Files\Tesseract-OCR"

_TESSERACT_HOME = os.environ.get(
    "TESSERACT_HOME",
    _DEFAULT_TESSERACT_HOME
)

_TESSERACT_CMD = os.environ.get(
    "TESSERACT_CMD",
    os.path.join(_TESSERACT_HOME, "tesseract.exe")
)

_TESSDATA_DIR = os.environ.get(
    "TESSERACT_TESSDATA_DIR",
    os.path.join(_TESSERACT_HOME, "tessdata")
)

_TESSERACT_LANG = os.environ.get(
    "TESSERACT_OCR_LANG",
    "eng"
)

_TESSERACT_DPI = int(
    os.environ.get("TESSERACT_OCR_DPI", "200")
)

# A stuck tesseract process is killed after this many seconds so it cannot
# block the request (and the calls queued behind it) forever.
_TESSERACT_TIMEOUT_SECONDS = int(
    os.environ.get("TESSERACT_TIMEOUT_SECONDS", "30")
)

pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD

if os.path.isdir(_TESSDATA_DIR):
    os.environ["TESSDATA_PREFIX"] = _TESSDATA_DIR


# =========================================================
# GLOBALS
# =========================================================

logger = logging.getLogger(__name__)

_PADDLE_OCR_ENGINE = None
# PaddleOCR is not thread-safe and the Functions host runs requests on a
# thread pool, so engine creation and inference must not overlap.
_PADDLE_LOCK = threading.Lock()

OCR_ENGINES = frozenset({"tesseract", "paddle"})

_PADDLE_MODELS_DIR = os.environ.get(
    "PADDLE_MODELS_DIR",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "paddle"
    )
)

_PADDLE_DET_MODEL_DIR = os.path.join(_PADDLE_MODELS_DIR, "det")
_PADDLE_REC_MODEL_DIR = os.path.join(_PADDLE_MODELS_DIR, "rec")
_PADDLE_CLS_MODEL_DIR = os.path.join(_PADDLE_MODELS_DIR, "cls")
_PADDLE_PDF_RENDER_SCALE = float(
    os.environ.get("PADDLE_PDF_RENDER_SCALE", "2.0")
)

_PADDLE_OCR_MAX_SIDE = int(
    os.environ.get("PADDLE_OCR_MAX_SIDE", "1600")
)

_PADDLE_LAYOUT_MODE = "paddle-layout"

_PDF_RENDER_DPI = float(
    os.environ.get("PDF_RENDER_DPI", str(_PADDLE_PDF_RENDER_SCALE * 72))
)


# =========================================================
# PDF HELPERS
# =========================================================

def open_pdf(pdf_bytes):
    return pypdfium2.PdfDocument(io.BytesIO(pdf_bytes))


def pdf_bytes_to_page_images(
    pdf_bytes: bytes,
    dpi: float | None = None,
    page_no: int | None = None,
) -> list[Image.Image]:
    """Render PDF page(s) to PIL RGB images.

    page_no is 1-based. When set, only that page is rendered.
    When None, every page is rendered.
    """
    if not pdf_bytes:
        raise ValueError("PDF bytes are empty.")

    render_dpi = _PDF_RENDER_DPI if dpi is None else dpi
    doc = open_pdf(pdf_bytes)

    try:
        page_count = len(doc)

        if page_no is not None:
            if page_no < 1 or page_no > page_count:
                raise ValueError(
                    f"page_no {page_no} is out of range "
                    f"(document has {page_count} page(s))"
                )

            page = doc[page_no - 1]
            try:
                bitmap = page.render(scale=render_dpi / 72)
                return [bitmap.to_pil().convert("RGB")]
            finally:
                page.close()

        renderer = doc.render(
            pypdfium2.PdfBitmap.to_pil,
            scale=render_dpi / 72,
        )
        return [img.convert("RGB") for img in renderer]
    finally:
        doc.close()


# =========================================================
# LAYOUT HELPERS
# =========================================================

def _estimate_layout_units(
    entries: list[dict[str, Any]]
) -> tuple[float, float, float]:
    heights = [entry["height"] for entry in entries]
    line_height = median(heights) if heights else 18.0

    left_edge = min(entry["left"] for entry in entries)
    right_edge = max(entry["right"] for entry in entries)
    content_width = max(right_edge - left_edge, 1.0)

    glyph_widths = []

    for entry in entries:
        glyph_count = len(re.sub(r"\s+", "", entry["text"]))

        if glyph_count > 0:
            glyph_widths.append(entry["width"] / glyph_count)

    fallback_glyph_width = max(content_width / 100.0, 4.0)
    glyph_width = median(glyph_widths) if glyph_widths else fallback_glyph_width
    # Keep glyph width tight so multi-column x positions stay separated.
    glyph_width = min(max(glyph_width, 3.5), max(content_width / 40.0, 6.0))

    return line_height, glyph_width, left_edge


def _same_layout_row(
    entry: dict[str, Any],
    row: dict[str, Any],
    row_threshold: float,
) -> bool:
    vertical_overlap = max(
        0.0,
        min(entry["bottom"], row["bottom"]) - max(entry["top"], row["top"])
    )
    min_height = min(entry["height"], row["height"])

    return (
        abs(entry["center_y"] - row["center_y"]) <= max(row_threshold, min_height * 0.45)
        or vertical_overlap >= min_height * 0.30
    )


def _group_entries_into_rows(
    entries: list[dict[str, Any]],
    line_height: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    row_threshold = max(line_height * 0.55, 8.0)

    for entry in sorted(entries, key=lambda item: (item["top"], item["left"])):
        for row in reversed(rows[-8:]):
            if _same_layout_row(entry, row, row_threshold):
                row["entries"].append(entry)
                row["top"] = min(row["top"], entry["top"])
                row["bottom"] = max(row["bottom"], entry["bottom"])
                row["height"] = max(row["height"], entry["height"])
                row["center_y"] = (row["top"] + row["bottom"]) / 2.0
                break
        else:
            rows.append(
                {
                    "entries": [entry],
                    "top": entry["top"],
                    "bottom": entry["bottom"],
                    "height": entry["height"],
                    "center_y": entry["center_y"],
                }
            )

    for row in rows:
        row["entries"].sort(key=lambda item: (item["left"], item["top"]))

    rows.sort(
        key=lambda row: (
            row["top"],
            min(item["left"] for item in row["entries"]),
        )
    )

    return rows


def _render_layout_row(
    row: dict[str, Any],
    left_edge: float,
    glyph_width: float,
) -> str:
    rendered = ""
    visual_end_col = 0
    # Large horizontal gaps → treat as separate columns with extra padding.
    column_gap_cols = max(4, int(round(18.0 / glyph_width)))

    for idx, entry in enumerate(row["entries"]):
        target_col = max(
            0,
            int(round((entry["left"] - left_edge) / glyph_width))
        )
        current_len = len(rendered)

        if idx > 0:
            prev = row["entries"][idx - 1]
            gap_px = entry["left"] - prev["right"]
            if gap_px > glyph_width * 8:
                target_col = max(
                    target_col,
                    current_len + column_gap_cols,
                    visual_end_col + column_gap_cols,
                )
            else:
                target_col = max(target_col, current_len + 1, visual_end_col)

        if target_col > current_len:
            rendered += " " * (target_col - current_len)

        rendered += entry["text"]
        visual_end_col = max(
            len(rendered),
            int(round((entry["right"] - left_edge) / glyph_width))
        )

    return rendered.rstrip()


def _layout_entries_to_text(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""

    line_height, glyph_width, left_edge = _estimate_layout_units(entries)
    rows = _group_entries_into_rows(entries, line_height)
    rendered_lines = []
    previous_bottom = None

    for row in rows:
        if previous_bottom is not None:
            gap = row["top"] - previous_bottom
            blank_lines = min(
                6,
                max(0, int(round(gap / line_height)) - 1)
            )
            rendered_lines.extend([""] * blank_lines)

        rendered_lines.append(
            _render_layout_row(row, left_edge, glyph_width)
        )
        previous_bottom = row["bottom"]

    return "\n".join(rendered_lines).rstrip()


# =========================================================
# PADDLE OCR HELPERS (paddleocr 2.7.x / paddlepaddle 2.6.x)
# =========================================================

def _ensure_paddle_model_dirs():
    missing = [
        path for path in (
            _PADDLE_DET_MODEL_DIR,
            _PADDLE_REC_MODEL_DIR,
            _PADDLE_CLS_MODEL_DIR,
        )
        if not os.path.isdir(path)
    ]

    if missing:
        raise FileNotFoundError(
            "Missing PaddleOCR model directories: "
            + ", ".join(missing)
            + ". Run: python download_paddle_models.py"
        )


def _configure_paddle_runtime():
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_use_onednn", "0")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")


def _prepare_image_for_paddle(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    width, height = rgb.size
    longest = max(width, height)

    if longest <= _PADDLE_OCR_MAX_SIDE:
        return rgb

    scale = _PADDLE_OCR_MAX_SIDE / longest
    resized = rgb.resize(
        (max(1, int(width * scale)), max(1, int(height * scale))),
        Image.LANCZOS,
    )

    logger.info(
        "Downscaled Paddle OCR input from %sx%s to %sx%s",
        width,
        height,
        resized.size[0],
        resized.size[1],
    )

    return resized


def _image_to_paddle_array(image: Image.Image) -> np.ndarray:
    prepared = _prepare_image_for_paddle(image)
    return np.ascontiguousarray(np.array(prepared, dtype=np.uint8))


def get_paddle_ocr_engine():
    global _PADDLE_OCR_ENGINE

    if _PADDLE_OCR_ENGINE is None:
        _configure_paddle_runtime()
        _ensure_paddle_model_dirs()

        from paddleocr import PaddleOCR

        _PADDLE_OCR_ENGINE = PaddleOCR(
            use_angle_cls=True,
            lang="en",
            use_gpu=False,
            show_log=False,
            det_model_dir=_PADDLE_DET_MODEL_DIR,
            rec_model_dir=_PADDLE_REC_MODEL_DIR,
            cls_model_dir=_PADDLE_CLS_MODEL_DIR,
        )

        logger.info(
            "Initialized PaddleOCR 2.x from %s",
            _PADDLE_MODELS_DIR,
        )

    return _PADDLE_OCR_ENGINE


def _run_paddle_ocr(ocr, image: Image.Image):
    arr = _image_to_paddle_array(image)
    # PaddleOCR 2.x: ocr.ocr(img, cls=True) -> list of pages of [box, (text, score)]
    return ocr.ocr(arr, cls=True)


def _paddle_box_to_layout_entry(
    text: str,
    box,
) -> dict[str, Any] | None:
    text = str(text or "").strip()

    if not text:
        return None

    try:
        arr = np.array(box, dtype=float)

        if arr.ndim == 1 and arr.size >= 4:
            left, top, right, bottom = arr[:4]
        else:
            points = arr.reshape(-1, 2)
            left = float(np.min(points[:, 0]))
            top = float(np.min(points[:, 1]))
            right = float(np.max(points[:, 0]))
            bottom = float(np.max(points[:, 1]))
    except Exception:
        return None

    return {
        "text": text,
        "left": float(left),
        "top": float(top),
        "right": float(right),
        "bottom": float(bottom),
        "width": max(float(right) - float(left), 1.0),
        "height": max(float(bottom) - float(top), 1.0),
        "center_y": (float(top) + float(bottom)) / 2.0,
    }


def _paddle_result_to_layout_text(pred_result) -> str:
    """Rebuild spaced plaintext from PaddleOCR 2.x box results."""
    entries = []
    fallback_lines = []

    if not pred_result:
        return ""

    for block in pred_result:
        if not block:
            continue

        for line in block:
            try:
                box = line[0]
                text = line[1][0]
            except Exception:
                continue

            entry = _paddle_box_to_layout_entry(text, box)

            if entry is not None:
                entries.append(entry)
            elif text:
                fallback_lines.append(str(text))

    if entries:
        return _layout_entries_to_text(entries)

    return "\n".join(fallback_lines).strip()


def _extract_page_with_paddle(image: Image.Image) -> str:
    with _PADDLE_LOCK:
        ocr = get_paddle_ocr_engine()
        pred = _run_paddle_ocr(ocr, image)
    return _paddle_result_to_layout_text(pred)


def extract_text_with_paddle(
    file_bytes: bytes,
    filename: str,
    page_no: int = 1,
) -> str:
    if not file_bytes:
        raise ValueError("File bytes are empty.")

    if page_no < 1:
        raise ValueError("page_no must be >= 1")

    logger.info(
        "Paddle extract start layout_mode=%s render_scale=%s max_side=%s "
        "page_no=%s file=%s models=%s",
        _PADDLE_LAYOUT_MODE,
        _PADDLE_PDF_RENDER_SCALE,
        _PADDLE_OCR_MAX_SIDE,
        page_no,
        filename,
        _PADDLE_MODELS_DIR,
    )

    ext = (os.path.splitext(filename)[1] or "").lower()
    kind = _detect_file_kind(file_bytes)

    supported_extensions = {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".tiff",
        ".webp",
    }

    if kind == "pdf":
        ext = ".pdf"
    elif kind == "image":
        ext = ext if ext in supported_extensions else ".png"
    elif ext not in supported_extensions:
        raise ValueError(f"Unsupported file type: {ext}")

    page_chunks = []

    if ext == ".pdf":
        try:
            page_images = pdf_bytes_to_page_images(
                file_bytes,
                _PADDLE_PDF_RENDER_SCALE * 72,
                page_no=page_no,
            )
        except Exception as exc:
            raise Exception(f"Failed to render PDF pages: {exc}") from exc

        last_error = None
        for image in page_images:
            try:
                logger.info("Paddle OCR page_no=%s", page_no)
                page_chunks.append(_extract_page_with_paddle(image))
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Paddle OCR failed on PDF page %s: %s",
                    page_no,
                    exc,
                )
                continue

        if not page_chunks and last_error is not None:
            raise Exception(
                f"Paddle OCR failed on page {page_no}: {last_error}"
            ) from last_error
    else:
        if page_no != 1:
            raise ValueError("page_no must be 1 for image files")

        try:
            image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        except Exception as exc:
            raise Exception(f"Failed to load image: {exc}") from exc

        try:
            page_chunks.append(_extract_page_with_paddle(image))
        except Exception as exc:
            raise Exception(f"OCR failed on image: {exc}") from exc

    final_text = "\f\n".join(chunk for chunk in page_chunks if chunk).strip()

    if not final_text:
        logger.info("Paddle OCR completed without extracted text.")

    return final_text


# =========================================================
# TESSERACT HELPERS
# =========================================================

def _ensure_tesseract():

    if not os.path.isfile(_TESSERACT_CMD):

        raise FileNotFoundError(
            f"Tesseract executable not found at {_TESSERACT_CMD}"
        )


def _tesseract_ocr_pil_image(img: Image.Image) -> str:

    _ensure_tesseract()

    rgb = img.convert("RGB")

    return pytesseract.image_to_string(
        rgb,
        lang=_TESSERACT_LANG,
        timeout=_TESSERACT_TIMEOUT_SECONDS,
    ).strip()


def _detect_file_kind(data: bytes) -> str:

    if data[:5] == b"%PDF-":
        return "pdf"

    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"

    if data[:3] == b"\xff\xd8\xff":
        return "image"

    return "unknown"


# =========================================================
# TESSERACT MAIN
# =========================================================

def tesseract_extract_text_from_bytes(
    data: bytes,
    page_no: int = 1,
) -> str:
    if page_no < 1:
        raise ValueError("page_no must be >= 1")

    kind = _detect_file_kind(data)

    if kind == "pdf":
        page_images = pdf_bytes_to_page_images(
            data,
            _TESSERACT_DPI,
            page_no=page_no,
        )

        page_chunks = [
            _tesseract_ocr_pil_image(img)
            for img in page_images
        ]

        return "\f\n".join(page_chunks) + "\n"

    if kind == "image":
        if page_no != 1:
            raise ValueError("page_no must be 1 for image files")

        img = Image.open(io.BytesIO(data))

        return (
            _tesseract_ocr_pil_image(img)
            + "\n"
        )

    raise ValueError(
        "Expected PDF or image"
    )