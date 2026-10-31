# Overview

**ez-ocr** is a small HTTP API for EZOFIS that:

1. **Extracts text from documents (OCR).** Upload a PDF or image and get plain text back. The text keeps the page layout (columns and spacing) as close as possible.
2. **Converts emails (.eml) to PDF.** Upload an `.eml` file and get an A4 PDF back, with From / To / Sent / Subject at the top and inline images included.

It runs as an **Azure Functions (Python) app inside a Docker container** on **Azure Container Apps**.

## Who uses it
Other EZOFIS services or clients call it over HTTP. No user interface, no login.
Which products call it: Unknown – needs confirmation.

## Endpoints (all under `/api`)

| Method | Path | Input (multipart/form-data) | Output |
|---|---|---|---|
| GET | `/api/health` | – | `OCR API running` |
| POST | `/api/extract_text` | `file`, `engine` = `tesseract` \| `paddle`, optional `page_no` (default 1) | `text/plain`, headers `X-OCR-Layout-Mode`, `X-OCR-Page-No` |
| POST | `/api/emltopdfconvert` | `file` (.eml) | `application/pdf` download |

Defined in [function_app.py](../../function_app.py).

## OCR engines
- **tesseract** – classic Tesseract OCR. PDF, PNG or JPEG.
- **paddle** – PaddleOCR 2.7. PDF, PNG, JPEG, BMP, TIFF, WEBP.

The Surya engine was removed on 2026-10-31 to cut hosting cost; `engine=surya` now returns 400.

Only one page is processed per request (`page_no`, 1-based). Image files must use `page_no=1`.
