# CLAUDE.md

## Project
**ez-ocr** is an EZOFIS HTTP API built on Azure Functions (Python 3.11) that runs in a Docker container on Azure Container Apps. It extracts layout-preserving text from PDFs and images with two OCR engines (Tesseract, PaddleOCR 2.7), and converts `.eml` emails to PDF with Playwright/Chromium. All endpoints are in `function_app.py`; all OCR logic is in `ocr.py`. No database, no UI, no tests yet.

## Key commands
```powershell
# PaddleOCR models into paddle/ (needed before docker build)
python download_paddle_models.py

# run locally (Functions Core Tools)
copy local.settings.json.example local.settings.json
func start

# build image
docker build -t ez-ocr .
```
Test / lint: none configured. Full setup in docs/ai/SETUP_AND_COMMANDS.md; Windows server without Docker in docs/ai/WINDOWS_SETUP.md.

## Endpoints
- `GET /api/health`
- `POST /api/extract_text` – multipart `file`, `engine` (tesseract|paddle), `page_no` (1-based, default 1)
- `POST /api/emltopdfconvert` – multipart `file` (.eml)

## Rules
- Never commit to `main`; use `feature/...`, `fix/...`, `chore/...`, `docs/...` branches.
- This repo is **public** (`tech-ezofis-git/ez-ocr`). Do not put Azure resource names, connection strings, image digests or customer files in commits, PRs or docs.
- Push with the `tech-ezofis-git` GitHub account.
- Never commit `local.settings.json`, `.env`, or model folders (`models/`, `paddle/`, `paddle_models/`).
- Keep the Paddle pins together: `paddlepaddle==2.6.1`, `paddleocr==2.7.3`, `numpy<2`, `protobuf<=3.20.2`, `opencv-contrib-python==4.6.0.66`.
- Surya OCR was removed on purpose (not needed, and it made the app very costly). Don't add it, torch or transformers back without asking.
- Set environment variables (`FLAGS_use_mkldnn`, protobuf mode) **before** importing paddle.
- Keep heavy imports inside handlers (lazy loading); cache models in module globals. Use PaddleOCR only under `_PADDLE_LOCK` and pypdfium2 only under `_PDFIUM_LOCK` (neither is thread-safe).
- EML → PDF renders untrusted HTML: keep JavaScript off and block every request except `data:` URLs.
- Never write files inside the app folder at runtime (`/home/site/wwwroot` in the container, the app folder on Windows) — the Functions host restarts on file changes. Use `OCR_TMP_DIR` or the system temp.
- Keep library versions pinned in `requirements.txt`; unpinned upgrades have broken the build before.
- Bad input → 400 with a short plain-text message. Log unexpected errors with `logging.exception`.
- The live app's settings differ from `azure/deploy-container-app.ps1`; don't run the script against production without checking.

## Reference docs
@docs/ai/OVERVIEW.md
@docs/ai/TECH_STACK.md
@docs/ai/ARCHITECTURE.md
@docs/ai/SETUP_AND_COMMANDS.md
@docs/ai/CONVENTIONS.md
@docs/ai/UPGRADE_NOTES.md
@docs/ai/WINDOWS_SETUP.md
