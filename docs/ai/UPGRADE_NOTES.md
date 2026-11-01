# Upgrade notes

Findings from a read-only review on 2026-10-31. Live checks were made against the running image and Container App, without sending any requests to it.

## Problems found

### Live setup
1. **Two Paddle model sets.** The live app sets `PADDLE_MODELS_DIR=/home/site/wwwroot/paddle_models` (PP-OCRv5 files). A live test on 2026-10-31 showed Paddle **does work** with it, so it was not broken as first assumed. The `fix/remove-surya` image uses the 2.x models in `paddle/` instead; output on test files was the same.
1a. **Tesseract calls sometimes hung in Azure (pre-existing) – fixed 2026-10-31.** Seen on 2026-10-31 on both the old and the new image: some calls get no response for 90 s+, and with several calls at once the new image stopped answering Tesseract entirely until restarted. Paddle and EML were not affected. Not reproducible locally (even pinned to 1 core). Root cause unknown – needs container logs / process list from inside the running replica. Defensive fix added on `fix/remove-surya`: `pytesseract.image_to_string(..., timeout=TESSERACT_TIMEOUT_SECONDS)` (default 30 s) kills a stuck process and returns 500 "Tesseract process timeout" instead of hanging. Tested locally with a fake stuck tesseract. Likely cause (unconfirmed): Tesseract 4.1.1 here is built with OpenMP (libgomp) and starts several threads per call regardless of `OMP_NUM_THREADS`; parallel processes on a small, shared CPU share can spin against each other. `ocr.py` now also sets `OMP_THREAD_LIMIT=1`, as the Tesseract docs recommend for parallel use. After deploying this, live tests with 3 and 8 simultaneous calls all passed in ~1–5 s.
2. ~~**Surya models are not in the image.**~~ Fixed on `fix/remove-surya`: Surya was removed.
3. **Image is about 2 GB and carries two Paddle model sets** (`paddle/` 16 MB and `paddle_models/` 99 MB), plus a stray PDF that got copied in by `COPY . .`.

### Security
4. **No authentication.** `http_auth_level=ANONYMOUS` and external ingress: anyone with the URL can use the API.
5. **No upload size limit.** Large files or big PDFs can use all memory/CPU.
6. **EML → PDF renders untrusted HTML in Chromium with `--no-sandbox`.** Fixed on `fix/pdfium-lock-eml-hardening`: three layers stop emails from making the server fetch remote or internal URLs (SSRF): a CSP `<meta>` as the first element of `<head>` (`_EML_CSP`), JavaScript off with every non-`data:` request aborted (`_allow_only_data_urls`), and Chromium on a dead proxy (`_EML_BROWSER_ARGS`), because `<link rel=prefetch>` bypasses `page.route()`. A 16-vector hostile email made 30 requests on the old image and 0 with all layers. `--no-sandbox` is still used.
7. **Error messages are returned to callers** (`OCR failed: <exception>`, `str(e)`), which can leak internal paths.

### Build and code
8. **Clean build fails**: `Dockerfile` requires `paddle/` but it is not in git; you must run `download_paddle_models.py` first. Also `.dockerignore` does not exclude `*.pdf` or `paddle/`-related junk.
9. ~~**`download_models.py` line 8 has a stray `J`**~~ Fixed: the script was deleted with Surya.
10. **Dead code in `function_app.py`**: a second module docstring halfway down, an unused `json` import, a commented-out root route inside a string, unused `parse_qs` import, `OCR_ENGINES` duplicated in `ocr.py`.
11. **`Dockerfile.paddle-fix`** builds on old `arasuezofis/ocr-app:v2` and the old `paddle_models` layout – likely obsolete.
12. **`azure/deploy-container-app.ps1` is out of date** vs the live app (image source, CPU/memory, `PADDLE_MODELS_DIR`). Running it as-is would downgrade resources and point at a Docker Hub image.
13. **Tesseract `_detect_file_kind`** only recognises PDF/PNG/JPEG; TIFF/BMP/WEBP fail with tesseract but work with paddle.
14. **Windows-only defaults** in `ocr.py` (Tesseract path). The forced `.tmp` temp folder inside the app folder was replaced by the optional `OCR_TMP_DIR` setting (it made the Functions host restart).
15. **`requests`** is installed but not used.

### Fixed on `fix/remove-surya` (found while load-testing)
- **Simultaneous Paddle calls failed** ("index out of bounds", model errors): PaddleOCR is not thread-safe and the Functions host runs requests on a thread pool. Now guarded by `_PADDLE_LOCK` in `ocr.py`.
- **Host restarted after OCR calls**, aborting in-flight requests ("Worker channel is shutting down"): `ocr.py` pointed TMP/TEMP into `wwwroot/.tmp`, and the host watches that folder. The redirect now only applies on Windows.
- **Unpinned packages broke the build** (Playwright 1.64 dropped Debian 11). Versions are now pinned to the live image's.

### Fixed on `fix/pdfium-lock-eml-hardening` (found on the Windows server test)
- **Parallel PDF calls crashed the worker** (`OSError: access violation` in `FPDF_RenderPageBitmap`, then heap corruption; every in-flight request failed). PDFium is not thread-safe, even across documents. All rendering in `pdf_bytes_to_page_images` now runs under `_PDFIUM_LOCK`. Seen on Windows with 8 parallel calls; the Linux container has the same code.

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
