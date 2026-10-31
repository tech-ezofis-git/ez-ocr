# Conventions

## Style
- Python 3.11, 4-space indent, no formatter or linter configured.
- `from __future__ import annotations` and type hints (`bytes`, `str | None`, `list[...]`) in `ocr.py` and the download scripts.
- Sections in `ocr.py` are separated by `# ====` banner comments.

## Naming
- `snake_case` functions and variables.
- Leading underscore = private helper (`_ensure_predictors`, `_detect_file_kind`).
- Module constants in `_UPPER_CASE`, usually read from environment variables with a default (`os.environ.get("X", "default")`).

## Patterns
- **Endpoints**: Azure Functions Python v2 decorators on one `app = func.FunctionApp(...)` in `function_app.py`. Auth level is `ANONYMOUS`.
- **Lazy loading**: heavy OCR imports happen inside the handler; models are created once and cached in module globals.
- **Thread safety**: requests run on a thread pool. Any shared model that is not thread-safe (PaddleOCR) must be used under a lock (`_PADDLE_LOCK`).
- **External processes get a timeout** (`TESSERACT_TIMEOUT_SECONDS`) and run with `OMP_THREAD_LIMIT=1`, so a stuck or slow tesseract cannot block other requests.
- **No writes inside the app folder** on Linux: the Functions host restarts when files in `/home/site/wwwroot` change. Use the system temp dir.
- **Env setup before imports**: Paddle/protobuf flags are set with `os.environ.setdefault` at the top of `function_app.py` and `ocr.py`, before paddle is imported. Keep this order.
- **Page numbers** are 1-based (`page_no`). One page per request.
- **Shared layout code**: any engine that returns boxes should build entries in the `_paddle_box_to_layout_entry` shape (text, left, top, right, bottom, width, height, center_y) and call `_layout_entries_to_text`.
- **Version pins**: Paddle stack is pinned (`paddlepaddle==2.6.1`, `paddleocr==2.7.3`, `numpy<2`, `protobuf<=3.20.2`, `opencv-contrib-python==4.6.0.66`). Change them together, never one at a time.

## Error handling
- Bad input → `HttpResponse(<message>, status_code=400)` with a short plain-text message.
- Engine code raises `ValueError` / `FileNotFoundError` / `Exception` with a clear message, often chained with `from exc`.
- The OCR handler catches everything, logs with `logging.exception`, and returns 500 with `type(e).__name__: message`.
- The EML handler returns 500 with `str(e)` and does **not** log.
- Download scripts print to stderr and exit with code 1 on failure.
