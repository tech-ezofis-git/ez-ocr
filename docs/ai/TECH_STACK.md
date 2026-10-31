# Tech stack

Versions marked "image" were read from the running container image (`pip list`, built 2026-08-10).
`requirements.txt` pins these to the live image's versions.
Surya OCR, torch and transformers were removed on branch `fix/remove-surya` (2026-10-31).

## Runtime
| Item | Version | Source |
|---|---|---|
| Python | 3.11.13 | image |
| Azure Functions host | 4.x (Python v2 programming model) | `Dockerfile` base `mcr.microsoft.com/azure-functions/python:4-python3.11` |
| OS | Debian 11 "bullseye" | base image |
| Tesseract binary | 4.1.1 | apt `tesseract-ocr` |
| Chromium (for PDF printing) | installed by `playwright install chromium` | `Dockerfile` |

## Python libraries
| Library | Pinned in requirements.txt | Installed (image) | Used for |
|---|---|---|---|
| azure-functions | `==1.24.0` | 1.24.0 | HTTP functions |
| paddlepaddle | `==2.6.1` | 2.6.1 | `paddle` engine |
| paddleocr | `==2.7.3` | 2.7.3 | `paddle` engine |
| protobuf | `<=3.20.2` in Dockerfile | 3.20.2 | Paddle needs old protobuf |
| numpy | `<2` | 1.26.4 | images / Paddle |
| opencv-contrib-python | `==4.6.0.66` | 4.6.0.66 (expected) | Paddle |
| pypdfium2 | `==4.30.0` | 4.30.0 | render PDF pages to images |
| pytesseract | `==0.3.13` | 0.3.13 | `tesseract` engine |
| Pillow | `==10.4.0` | 10.4.0 | images |
| playwright | `==1.61.0` | 1.61.0 | EML → PDF |
| requests | `==2.34.2` | 2.34.2 | Not used in code – needs confirmation |

## Database / storage
No database. Azure Functions needs `AzureWebJobsStorage` (an Azure Storage account). The app itself does not store files.

## Hosting
- **Azure Container Apps** (region South India).
- Live settings (since 2026-10-31): 1 vCPU, 2 GiB, 1–3 replicas, single-revision mode, TCP probes on port 80. Was 4 vCPU / 8 GiB with Surya.
- Image registry in use: `ghcr.io/tech-ezofis-git/ez-ocr` (tags `no-surya-<commit>`). The old Azure Container Registry entry was removed (the registry no longer existed and blocked updates).
- Older images came from Docker Hub `arasuezofis/ocr-app` (see [azure/deploy-container-app.ps1](../../azure/deploy-container-app.ps1)).
- CI/CD: none in this repo. How the GHCR image is built and pushed: Unknown – needs confirmation.
