# Setup and commands

## Local run (Windows)
Needs Python 3.11, [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local), Tesseract installed at `C:\Program Files\Tesseract-OCR`, and Azurite (or a storage connection string).

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install "numpy<2"
pip install "paddlepaddle==2.6.1" -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install -r requirements.txt
pip install "protobuf<=3.20.2"
python -m playwright install chromium
copy local.settings.json.example local.settings.json
func start
```
Then call `http://localhost:7071/api/health`.
Not verified in this analysis – Python is not installed on the analysis machine.

## Download PaddleOCR models (before building the image)
```powershell
python download_paddle_models.py  # PaddleOCR 2.7.3 → paddle/  (deletes paddle/ first)
```

## Build the image
```bash
docker build -t ez-ocr .
```
The build **fails** unless `paddle/det`, `paddle/rec`, `paddle/cls` exist (check at the end of the `Dockerfile`).

## Run the image locally
```bash
docker run --rm -p 8080:80 -e AzureWebJobsStorage="<connection string>" ez-ocr
```

## Example calls
```bash
curl -F file=@sample.pdf -F engine=tesseract -F page_no=1 http://localhost:8080/api/extract_text
```
```bash
curl -F file=@mail.eml -o mail.pdf http://localhost:8080/api/emltopdfconvert
```

## Test / lint
None. The repo has no tests, no linter config, and no CI.

## Deploy
[azure/deploy-container-app.ps1](../../azure/deploy-container-app.ps1) creates the resource group, ACR, storage, Log Analytics, Container Apps environment and the app, then imports an image into ACR.
```powershell
cd azure
.\deploy-container-app.ps1 -SourceImage "<image>" -AcrTag "<tag>"
```
Warning: its defaults (Docker Hub image, 2 CPU / 4 GiB, `PADDLE_MODELS_DIR=paddle_models`) do not match the live app, which runs a GHCR image with 4 CPU / 8 GiB. How the live app is actually deployed: Unknown – needs confirmation.
