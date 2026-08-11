from __future__ import annotations

import io
import logging
import os
import re
import sys
from statistics import median
from typing import Any


# =========================================================
# ENVIRONMENT SETUP
# (must run BEFORE importing surya / transformers / torch
#  because those libraries read cache env vars at import time)
# =========================================================

if "APP_PATH" in os.environ:
    app_path = os.path.abspath(os.environ["APP_PATH"])

    if os.getcwd() != app_path:
        os.chdir(app_path)

    if app_path not in sys.path:
        sys.path.append(app_path)


_MODELS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "models"
)

os.makedirs(_MODELS_DIR, exist_ok=True)

os.environ.setdefault("MODEL_CACHE_DIR", _MODELS_DIR)
os.environ.setdefault("HF_HOME", os.path.join(_MODELS_DIR, "hf"))
os.environ.setdefault("HF_HUB_CACHE", os.path.join(_MODELS_DIR, "hf", "hub"))
os.environ.setdefault(
    "TRANSFORMERS_CACHE",
    os.path.join(_MODELS_DIR, "hf", "transformers"),
)
os.environ.setdefault("TORCH_HOME", os.path.join(_MODELS_DIR, "torch"))


import cv2
import fitz
import numpy as np
import pypdfium2
import pytesseract

from PIL import Image

from surya.common.surya.schema import TaskNames
from surya.common.util import rescale_bbox
from surya.models import load_predictors
from surya.settings import settings


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

pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD

if os.path.isdir(_TESSDATA_DIR):
    os.environ["TESSDATA_PREFIX"] = _TESSDATA_DIR


# =========================================================
# GLOBALS
# =========================================================

logger = logging.getLogger(__name__)

predictors = None
_PADDLE_OCR_ENGINE = None

OCR_ENGINES = frozenset({"surya", "tesseract", "paddle"})

_PADDLE_MODELS_DIR = os.environ.get(
    "PADDLE_MODELS_DIR",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "paddle_models"
    )
)

_PADDLE_DET_MODEL_DIR = os.path.join(_PADDLE_MODELS_DIR, "det")
_PADDLE_REC_MODEL_DIR = os.path.join(_PADDLE_MODELS_DIR, "rec")
_PADDLE_CLS_MODEL_DIR = os.path.join(_PADDLE_MODELS_DIR, "cls")
_PADDLE_PDF_RENDER_SCALE = float(
    os.environ.get("PADDLE_PDF_RENDER_SCALE", "3")
)


# =========================================================
# SURYA MODEL LOADER
# =========================================================

def _ensure_predictors():
    global predictors

    if predictors is None:
        predictors = load_predictors()


# =========================================================
# PDF HELPERS
# =========================================================

def open_pdf(pdf_bytes):
    return pypdfium2.PdfDocument(io.BytesIO(pdf_bytes))


def render_page(pdf_bytes, page_index, dpi):
    doc = open_pdf(pdf_bytes)

    renderer = doc.render(
        pypdfium2.PdfBitmap.to_pil,
        page_indices=[page_index],
        scale=dpi / 72,
    )

    img = list(renderer)[0].convert("RGB")

    doc.close()

    return img


# =========================================================
# OCR HELPERS
# =========================================================

_SCRIPT_TAG_RE = re.compile(r"<SCRIPT-[A-Z]+>")
_INLINE_TAG_RE = re.compile(r"</?[A-Za-z][^>\n]*>")


def _strip_ocr_markup(text: str) -> str:
    if not text:
        return text

    text = _SCRIPT_TAG_RE.sub("", text)
    text = _INLINE_TAG_RE.sub("", text)

    return text.strip()


def _ocr_math_mode() -> bool:
    v = os.environ.get(
        "SURYA_OCR_MATH_MODE",
        "true"
    ).lower()

    return v in ("1", "true", "yes")


def _line_to_layout_entry(line) -> dict[str, Any] | None:
    text = _strip_ocr_markup((line.text or "").strip())

    if not text:
        return None

    left, top, right, bottom = [float(v) for v in line.bbox]

    return {
        "text": text,
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": max(right - left, 1.0),
        "height": max(bottom - top, 1.0),
        "center_y": (top + bottom) / 2.0,
    }


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

    fallback_glyph_width = max(content_width / 120.0, 5.0)
    glyph_width = median(glyph_widths) if glyph_widths else fallback_glyph_width
    glyph_width = min(max(glyph_width, 4.0), max(content_width / 20.0, 8.0))

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
    row_threshold = max(line_height * 0.60, 8.0)

    for entry in sorted(entries, key=lambda item: (item["top"], item["left"])):
        for row in reversed(rows):
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

    for idx, entry in enumerate(row["entries"]):
        target_col = max(
            0,
            int(round((entry["left"] - left_edge) / glyph_width))
        )
        current_len = len(rendered)

        if idx > 0:
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


def _ocr_result_to_layout_text(ocr_result) -> str:
    entries = []

    for line in ocr_result.text_lines:
        entry = _line_to_layout_entry(line)

        if entry is not None:
            entries.append(entry)

    return _layout_entries_to_text(entries)


def run_ocr(img, highres_img):
    _ensure_predictors()

    result = predictors["recognition"](
        [img],
        task_names=[TaskNames.ocr_with_boxes],
        det_predictor=predictors["detection"],
        highres_images=[highres_img],
        return_words=False,
        math_mode=_ocr_math_mode(),
        sort_lines=True,
    )[0]

    return result


# =========================================================
# SURYA TEXT EXTRACTION
# =========================================================

def pdf_bytes_to_layout_text(pdf_bytes: bytes) -> str:
    doc = open_pdf(pdf_bytes)

    page_count = len(doc)

    doc.close()

    page_chunks = []

    for page_index in range(page_count):

        img = render_page(
            pdf_bytes,
            page_index,
            settings.IMAGE_DPI
        )

        hi = render_page(
            pdf_bytes,
            page_index,
            settings.IMAGE_DPI_HIGHRES
        )

        ocr_result = run_ocr(img, hi)
        page_chunks.append(
            _ocr_result_to_layout_text(ocr_result)
        )

    return "\f\n".join(page_chunks) + "\n"


# =========================================================
# PADDLE OCR HELPERS
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
        )


def get_paddle_ocr_engine():
    global _PADDLE_OCR_ENGINE

    if _PADDLE_OCR_ENGINE is None:
        _ensure_paddle_model_dirs()

        from paddleocr import PaddleOCR

        try:
            _PADDLE_OCR_ENGINE = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=True,
                enable_mkldnn=False,
                text_detection_model_name="PP-OCRv5_server_det",
                text_detection_model_dir=_PADDLE_DET_MODEL_DIR,
                text_recognition_model_name="en_PP-OCRv5_mobile_rec",
                text_recognition_model_dir=_PADDLE_REC_MODEL_DIR,
                textline_orientation_model_name="PP-LCNet_x1_0_textline_ori",
                textline_orientation_model_dir=_PADDLE_CLS_MODEL_DIR,
            )
        except (TypeError, ValueError):
            _PADDLE_OCR_ENGINE = PaddleOCR(
                use_angle_cls=True,
                lang="en",
                det_model_dir=_PADDLE_DET_MODEL_DIR,
                rec_model_dir=_PADDLE_REC_MODEL_DIR,
                cls_model_dir=_PADDLE_CLS_MODEL_DIR,
            )

    return _PADDLE_OCR_ENGINE


def _run_paddle_ocr(ocr, image: Image.Image):
    arr = np.array(image.convert("RGB"))

    if hasattr(ocr, "predict"):
        return ocr.predict(arr)

    return ocr.ocr(arr)


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


def _fitz_word_to_layout_entry(word) -> dict[str, Any] | None:
    try:
        left, top, right, bottom, text = word[:5]
    except Exception:
        return None

    return _paddle_box_to_layout_entry(
        str(text),
        [left, top, right, bottom]
    )


def _pdf_page_embedded_layout_text(page) -> str:
    try:
        words = page.get_text("words")
    except Exception:
        return ""

    entries = []

    for word in words:
        entry = _fitz_word_to_layout_entry(word)

        if entry is not None:
            entries.append(entry)

    return _layout_entries_to_text(entries)


def _paddle_result_to_layout_text(pred_result) -> str:
    entries = []
    fallback_lines = []

    if not pred_result:
        return ""

    for block in pred_result:
        if hasattr(block, "get"):
            rec_texts = block.get("rec_texts") or []
            boxes = block.get("rec_boxes")

            if boxes is None:
                boxes = block.get("rec_polys")

            if boxes is not None:
                for text, box in zip(rec_texts, boxes):
                    entry = _paddle_box_to_layout_entry(text, box)

                    if entry is not None:
                        entries.append(entry)

            fallback_lines.extend(str(text) for text in rec_texts if text)
            continue

        if not block:
            continue

        for line in block:
            try:
                text = line[1][0]
                box = line[0]
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


def _extract_text_lines_from_paddle_result(pred_result):
    lines = []

    if not pred_result:
        return lines

    for block in pred_result:
        rec_texts = None

        if hasattr(block, "get"):
            rec_texts = block.get("rec_texts")

        if rec_texts:
            lines.extend(str(text) for text in rec_texts if text)
            continue

        if not block:
            continue

        for line in block:
            try:
                text = line[1][0]

                if text:
                    lines.append(str(text))
            except Exception:
                continue

    return lines


def extract_text_with_paddle(file_bytes: bytes, filename: str) -> str:
    if not file_bytes:
        raise ValueError("File bytes are empty.")

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
            pdf_document = fitz.open(
                stream=file_bytes,
                filetype="pdf"
            )
        except Exception as exc:
            raise Exception(f"Failed to open PDF: {exc}") from exc

        try:
            for page_number, page in enumerate(pdf_document):
                try:
                    embedded_text = _pdf_page_embedded_layout_text(page)

                    if embedded_text:
                        page_chunks.append(embedded_text)
                        continue

                    ocr = get_paddle_ocr_engine()
                    matrix = fitz.Matrix(
                        _PADDLE_PDF_RENDER_SCALE,
                        _PADDLE_PDF_RENDER_SCALE,
                    )
                    pix = page.get_pixmap(matrix=matrix)
                    img_bytes = pix.tobytes("png")
                    image = Image.open(io.BytesIO(img_bytes)).convert("RGB")

                    pred = _run_paddle_ocr(ocr, image)
                    page_chunks.append(
                        _paddle_result_to_layout_text(pred)
                    )
                except Exception as exc:
                    logger.warning(
                        "Paddle OCR failed on PDF page %s: %s",
                        page_number + 1,
                        exc,
                    )
                    continue
        finally:
            pdf_document.close()
    else:
        try:
            image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        except Exception as exc:
            raise Exception(f"Failed to load image: {exc}") from exc

        try:
            ocr = get_paddle_ocr_engine()
            pred = _run_paddle_ocr(ocr, image)
            page_chunks.append(_paddle_result_to_layout_text(pred))
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
        lang=_TESSERACT_LANG
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
    data: bytes
) -> str:

    kind = _detect_file_kind(data)

    if kind == "pdf":

        doc = open_pdf(data)

        page_count = len(doc)

        doc.close()

        page_chunks = []

        for page_index in range(page_count):

            img = render_page(
                data,
                page_index,
                _TESSERACT_DPI
            )

            page_chunks.append(
                _tesseract_ocr_pil_image(img)
            )

        return "\f\n".join(page_chunks) + "\n"

    if kind == "image":

        img = Image.open(io.BytesIO(data))

        return (
            _tesseract_ocr_pil_image(img)
            + "\n"
        )

    raise ValueError(
        "Expected PDF or image"
    )