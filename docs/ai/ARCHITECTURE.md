# Architecture

## Folder structure
```
function_app.py            HTTP endpoints (OCR, health, EML→PDF) – the entry point
ocr.py                     All OCR logic: Tesseract, PaddleOCR + layout rebuilding
download_paddle_models.py  Downloads PaddleOCR 2.7.3 models into paddle/ (run before docker build)
Dockerfile                 Main image build
Dockerfile.paddle-fix      Old patch image: adds paddle_models/ on top of arasuezofis/ocr-app:v2
host.json                  Azure Functions host config (version 2.0 only)
local.settings.json.example  Local Functions settings template
azure/deploy-container-app.ps1  Creates/updates the Azure resources and the container app
azure/README.md            Deploy notes
```
Model folders `paddle/` and `paddle_models/` are not in git. The image uses `paddle/` (PaddleOCR 2.x format).

## How it starts
The Azure Functions host (inside the base image) loads `function_app.py` from `/home/site/wwwroot` and listens on port 80.
`ocr.py` is imported **inside** the OCR handler, so the heavy libraries load on the first OCR request, not at startup.

## Request flow: `POST /api/extract_text`
1. `function_app.ocr` reads `file`, `engine`, `page_no` and checks them (400 on bad input).
2. Calls one engine in `ocr.py`:
   - `tesseract_extract_text_from_bytes` → render PDF page (200 DPI) or open image → `pytesseract.image_to_string`.
   - `extract_text_with_paddle` → render page at scale 2.0 → shrink to max 1600 px → `PaddleOCR.ocr` → `_paddle_result_to_layout_text`.
3. Paddle results go through the shared layout code (`_layout_entries_to_text`): boxes are grouped into rows and placed by x position with spaces, so columns line up.
4. Returns plain text. Any exception → 500 with the error message.

The Paddle model is loaded once per process and kept in the global `_PADDLE_OCR_ENGINE`.

## Request flow: `POST /api/emltopdfconvert`
1. `parse_eml_bytes` parses the email, picks the largest HTML (or plain text) part, swaps `cid:` images for base64 data URIs, adds a header block.
2. `html_to_pdf_bytes` opens headless Chromium (Playwright), loads the HTML, prints A4 PDF.
3. Returns the PDF as an attachment named after the uploaded file.

## External services
- No outside API calls at request time (Paddle models are baked into the image).
- Azure Storage (`AzureWebJobsStorage`) for the Functions host.

## Configuration (environment variables)
| Variable | Default | Used in |
|---|---|---|
| `PADDLE_MODELS_DIR` | `./paddle` | `ocr.py` |
| `PADDLE_PDF_RENDER_SCALE` / `PADDLE_OCR_MAX_SIDE` | `2.0` / `1600` | `ocr.py` |
| `PDF_RENDER_DPI` | 144 | `ocr.py` |
| `TESSERACT_CMD`, `TESSERACT_HOME`, `TESSERACT_TESSDATA_DIR` | Windows path `C:\Program Files\Tesseract-OCR` | `ocr.py` |
| `TESSERACT_OCR_LANG` / `TESSERACT_OCR_DPI` | `eng` / `200` | `ocr.py` |
| `APP_PATH` | – | `ocr.py` (changes working dir) |
