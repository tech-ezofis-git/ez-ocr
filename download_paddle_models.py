from __future__ import annotations

import sys
import shutil
from pathlib import Path


HERE = Path(__file__).parent.resolve()
PADDLE_MODELS_DIR = HERE / "paddle_models"

DET_MODEL_DIR = PADDLE_MODELS_DIR / "det"
REC_MODEL_DIR = PADDLE_MODELS_DIR / "rec"
CLS_MODEL_DIR = PADDLE_MODELS_DIR / "cls"

PADDLE_CACHE_DIR = Path.home() / ".paddlex" / "official_models"

PADDLE_MODEL_SOURCES = {
    DET_MODEL_DIR: "PP-OCRv5_server_det",
    REC_MODEL_DIR: "en_PP-OCRv5_mobile_rec",
    CLS_MODEL_DIR: "PP-LCNet_x1_0_textline_ori",
}


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


def main() -> int:
    print(f"Target Paddle models dir : {PADDLE_MODELS_DIR}")
    print("Downloading PaddleOCR English models (this may take a while)...\n")

    try:
        from paddleocr import PaddleOCR
    except Exception as exc:
        print(
            "Failed to import PaddleOCR. Install paddleocr and paddlepaddle first.",
            file=sys.stderr,
        )
        print(f"Import error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            lang="en",
            text_detection_model_name=PADDLE_MODEL_SOURCES[DET_MODEL_DIR],
            text_recognition_model_name=PADDLE_MODEL_SOURCES[REC_MODEL_DIR],
            textline_orientation_model_name=PADDLE_MODEL_SOURCES[CLS_MODEL_DIR],
        )
    except (TypeError, ValueError):
        PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_angle_cls=True,
            lang="en",
            det_model_name=PADDLE_MODEL_SOURCES[DET_MODEL_DIR],
            rec_model_name=PADDLE_MODEL_SOURCES[REC_MODEL_DIR],
            cls_model_name=PADDLE_MODEL_SOURCES[CLS_MODEL_DIR],
        )
    except Exception as exc:
        print(f"\nDownload failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    for target_dir, model_name in PADDLE_MODEL_SOURCES.items():
        source_dir = PADDLE_CACHE_DIR / model_name

        if not source_dir.is_dir():
            print(
                f"Downloaded model folder not found: {source_dir}",
                file=sys.stderr,
            )
            return 1

        if target_dir.exists():
            shutil.rmtree(target_dir)

        shutil.copytree(source_dir, target_dir)

    file_count, total_size = _dir_stats(PADDLE_MODELS_DIR)

    print("\nDone.")
    print(f"Files : {file_count}")
    print(f"Size  : {total_size / (1024 ** 2):.2f} MB")
    print(f"Path  : {PADDLE_MODELS_DIR}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
