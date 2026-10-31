from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


HERE = Path(__file__).parent.resolve()
PADDLE_DIR = HERE / "paddle"

DET_MODEL_DIR = PADDLE_DIR / "det"
REC_MODEL_DIR = PADDLE_DIR / "rec"
CLS_MODEL_DIR = PADDLE_DIR / "cls"


def _dir_stats(path: Path) -> tuple[int, int]:
    total_size = 0
    file_count = 0

    if not path.exists():
        return file_count, total_size

    for item in path.rglob("*"):
        if item.is_file():
            file_count += 1
            total_size += item.stat().st_size

    return file_count, total_size


def _looks_like_model_dir(path: Path) -> bool:
    if not path.is_dir():
        return False

    names = {p.name.lower() for p in path.iterdir() if p.is_file()}
    has_params = any(
        name.endswith(".pdiparams") or name == "inference.pdiparams"
        for name in names
    )
    has_model = any(
        name.endswith(".pdmodel")
        or name == "inference.pdmodel"
        or name.endswith(".json")
        for name in names
    )
    return has_params and has_model


def _resolve_model_dir(raw: str | None, label: str) -> Path:
    if not raw:
        raise FileNotFoundError(f"PaddleOCR did not expose a {label} model dir")

    path = Path(raw).expanduser().resolve()
    if not _looks_like_model_dir(path):
        raise FileNotFoundError(
            f"Downloaded {label} model dir looks invalid: {path}"
        )
    return path


def main() -> int:
    print(f"Target Paddle models dir : {PADDLE_DIR}")
    print("Downloading PaddleOCR 2.7.3 English models...\n")

    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_use_onednn", "0")

    try:
        from paddleocr import PaddleOCR
    except Exception as exc:
        print(
            "Failed to import PaddleOCR. Install paddleocr==2.7.3 "
            "and paddlepaddle==2.6.1 first.",
            file=sys.stderr,
        )
        print(f"Import error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        ocr = PaddleOCR(
            use_angle_cls=True,
            lang="en",
            use_gpu=False,
            show_log=True,
        )
    except Exception as exc:
        print(f"\nDownload failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    # PaddleOCR 2.x keeps resolved dirs on the engine / args object.
    det_src = _resolve_model_dir(
        getattr(ocr, "det_model_dir", None)
        or getattr(getattr(ocr, "args", None), "det_model_dir", None),
        "det",
    )
    rec_src = _resolve_model_dir(
        getattr(ocr, "rec_model_dir", None)
        or getattr(getattr(ocr, "args", None), "rec_model_dir", None),
        "rec",
    )
    cls_src = _resolve_model_dir(
        getattr(ocr, "cls_model_dir", None)
        or getattr(getattr(ocr, "args", None), "cls_model_dir", None),
        "cls",
    )

    print(f"det source: {det_src}")
    print(f"rec source: {rec_src}")
    print(f"cls source: {cls_src}")

    if PADDLE_DIR.exists():
        shutil.rmtree(PADDLE_DIR)

    PADDLE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(det_src, DET_MODEL_DIR)
    shutil.copytree(rec_src, REC_MODEL_DIR)
    shutil.copytree(cls_src, CLS_MODEL_DIR)

    file_count, total_size = _dir_stats(PADDLE_DIR)

    print("\nDone.")
    print(f"Files : {file_count}")
    print(f"Size  : {total_size / (1024 ** 2):.2f} MB")
    print(f"Path  : {PADDLE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
