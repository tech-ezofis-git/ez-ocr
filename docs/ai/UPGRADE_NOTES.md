# Upgrade notes

Findings from a read-only review on 2026-10-31. Live checks were made against the running image and Container App, without sending any requests to it.

## Problems found

### Live setup
1. **Paddle model mismatch in production.** The live app sets `PADDLE_MODELS_DIR=/home/site/wwwroot/paddle_models`. That folder holds **PP-OCRv5** models (PaddleOCR 3.x format: `inference.json`/`inference.yml`, no `.pdmodel`), but the code uses **paddleocr 2.7.3**, which expects the 2.x models in `paddle/`. The `paddle` engine probably fails in production. Unknown – needs confirmation with a test request.
2. ~~**Surya models are not in the image.**~~ Fixed on `fix/remove-surya`: Surya was removed.
3. **Image is about 2 GB and carries two Paddle model sets** (`paddle/` 16 MB and `paddle_models/` 99 MB), plus a stray PDF that got copied in by `COPY . .`.

### Security
4. **No authentication.** `http_auth_level=ANONYMOUS` and external ingress: anyone with the URL can use the API.
5. **No upload size limit.** Large files or big PDFs can use all memory/CPU.
6. **EML → PDF renders untrusted HTML in Chromium with `--no-sandbox`.** Remote images/scripts in emails can be loaded (external requests from the server). Consider blocking network requests and JavaScript in Playwright.
7. **Error messages are returned to callers** (`OCR failed: <exception>`, `str(e)`), which can leak internal paths.

### Build and code
8. **Clean build fails**: `Dockerfile` requires `paddle/` but it is not in git; you must run `download_paddle_models.py` first. Also `.dockerignore` does not exclude `*.pdf` or `paddle/`-related junk.
9. ~~**`download_models.py` line 8 has a stray `J`**~~ Fixed: the script was deleted with Surya.
10. **Dead code in `function_app.py`**: a second module docstring halfway down, an unused `json` import, a commented-out root route inside a string, unused `parse_qs` import, `OCR_ENGINES` duplicated in `ocr.py`.
11. **`Dockerfile.paddle-fix`** builds on old `arasuezofis/ocr-app:v2` and the old `paddle_models` layout – likely obsolete.
12. **`azure/deploy-container-app.ps1` is out of date** vs the live app (image source, CPU/memory, `PADDLE_MODELS_DIR`). Running it as-is would downgrade resources and point at a Docker Hub image.
13. **Tesseract `_detect_file_kind`** only recognises PDF/PNG/JPEG; TIFF/BMP/WEBP fail with tesseract but work with paddle.
14. **Windows-only defaults** in `ocr.py` (Tesseract path, `TMP`/`TEMP` forced to `.tmp`).
15. **`requests`** is installed but not used.

### Fixed on `fix/remove-surya` (found while load-testing)
- **Simultaneous Paddle calls failed** ("index out of bounds", model errors): PaddleOCR is not thread-safe and the Functions host runs requests on a thread pool. Now guarded by `_PADDLE_LOCK` in `ocr.py`.
- **Host restarted after OCR calls**, aborting in-flight requests ("Worker channel is shutting down"): `ocr.py` pointed TMP/TEMP into `wwwroot/.tmp`, and the host watches that folder. The redirect now only applies on Windows.
- **Unpinned packages broke the build** (Playwright 1.64 dropped Debian 11). Versions are now pinned to the live image's.

### Tests and process
16. **No tests, no linter, no CI.** The deployed code was not in git until PR #1 (`chore/sync-deployed-code`).

## Outdated / risky dependencies
| Item | Now | Note | Risk to upgrade |
|---|---|---|---|
| Base image | bullseye (Debian 11) | Debian 11 LTS ended Aug 2026; move to bookworm-based Functions image | medium |
| Tesseract | 4.1.1 | 5.x is current, better accuracy | medium |
| paddleocr / paddlepaddle | 2.7.3 / 2.6.1 | Old 2.x line; 3.x has a different API and model format | high |
| protobuf | 3.20.2 | Very old, forced by Paddle 2.x | high (tied to Paddle) |
| numpy | 1.26.4 (`<2`) | Forced by Paddle 2.x / OpenCV 4.6 | high (tied to Paddle) |
| opencv-contrib-python | 4.6.0.66 | Old; `-headless` would be smaller for a server | medium |
| Unpinned libs (playwright, Pillow, pypdfium2, azure-functions, pytesseract) | – | Rebuilds can silently pull new versions | low to pin |

## Suggested order
1. **Low** – Pin the remaining unpinned versions (most were pinned on `fix/remove-surya`).
2. **Low** – Remove dead code in `function_app.py`. (`*.pdf` was added to `.dockerignore` on `fix/remove-surya`.)
3. **Low** – Add a few tests (input validation in `function_app.py`, `_layout_entries_to_text`, `parse_eml_bytes`).
4. **Medium** – Fix production `PADDLE_MODELS_DIR` (point to `paddle`) or move to models that match; remove the unused model set.
5. **Medium** – Add authentication (function key or API key) and an upload size limit; stop returning raw exception text.
6. **Medium** – Lock down Playwright for EML (block network/JS) and log EML errors.
7. **Medium** – Update the deploy script to match the live app, or add a CI workflow that builds and deploys the GHCR image.
8. **Medium** – Move to a bookworm-based Functions image and Tesseract 5.
9. **High** – Upgrade PaddleOCR to 3.x (new API, new models, unpins numpy/protobuf). Do it as its own project with before/after OCR comparisons.
