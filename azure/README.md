# Azure Container Apps deployment

Deploy to **Azure Container Apps** using **ACR** (recommended). The script imports your Docker Hub image into ACR.

## Prerequisites

1. [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
2. Logged in: `az login`
3. Subscription selected: `az account set --subscription "<id>"`

## Quick deploy

```powershell
cd azure
.\deploy-container-app.ps1
```

Optional parameters:

```powershell
.\deploy-container-app.ps1 `
    -ResourceGroup "rg-ezc-api" `
    -Location "southindia" `
    -AppName "ez-container-app" `
    -SourceImage "docker.io/arasuezofis/ocr-app:v1" `
    -AcrName "<your-acr-name>" `
    -Cpu 2.0 `
    -Memory "4Gi" `
    -MinReplicas 1 `
    -MaxReplicas 5
```

## Defaults

| Setting | Value |
| ------- | ----- |
| Source image | `docker.io/arasuezofis/ocr-app:v1` |
| Port | `80` |
| CPU / memory | `2.0` / `4Gi` |
| Min replicas | `1` (avoids cold start on every request) |

## API endpoints

After deploy, use the printed FQDN:

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/api/health` | Health check |
| POST | `/api/extract_text` | OCR (`file`, `engine`) |
| POST | `/api/emltopdfconvert` | EML → PDF (`file`) |

## Update image after a new push

```powershell
.\deploy-container-app.ps1 -AcrTag "v2"
```

## Private Docker Hub

If the source image is private, `az acr import` needs credentials. Easiest is to make the source image public or push your image to ACR from Docker locally.

## Notes

- **AzureWebJobsStorage** is required because the image runs the Azure Functions host.
- First request after scale-from-zero can be slow (large OCR models).
- Use **MinReplicas 1** in production for steadier latency.
- Ensure **Surya models** are baked into the image (`python download_models.py` before `docker build`).
