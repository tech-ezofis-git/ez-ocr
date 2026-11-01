# Run ez-ocr on Windows Server (no Docker)

Runs the **same code** as the container (`function_app.py`, `ocr.py`) directly on Windows Server 2019 or newer, as a Windows service behind IIS.
Windows Server 2012 R2 is **not** supported (no Chromium for EML → PDF, no security updates).

Everything goes into `C:\ezocr` and `C:\Python311`. **No installers are run**, so existing Python, Tesseract and IIS apps on a shared server are not touched.
Run every step in an **elevated PowerShell**. Nothing here touches existing IIS sites until step 9.

## Versions (must match the container, so OCR output is the same)
| Item | Version | Source |
|---|---|---|
| Python | 3.11.9 x64 | NuGet package `python` 3.11.9, unzipped (no installer, registry or PATH change) |
| Azure Functions Core Tools | 4.x | portable ZIP `Azure.Functions.Cli.win-x64.<version>.zip` (GitHub releases), unzipped to `C:\ezocr\func` |
| Tesseract | 4.1.0 (`elag2019`) | UB Mannheim `tesseract-ocr-w64-setup-v4.1.0-elag2019.exe`, **extracted with 7-Zip, never run**. No Windows build of 4.1.1 (the container's version) exists; output was compared and matches |
| `eng.traineddata` | `tessdata_fast` tag **4.1.0** | SHA-256 `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2` – identical to the container's file |
| Python packages | `requirements.txt` | plus `paddlepaddle==2.6.1` from the Paddle CPU index and `protobuf<=3.20.2` |
| PaddleOCR models | 2.x format in `paddle\` | from `download_paddle_models.py` |

## 1. Check the server first (read-only)
```powershell
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, FreePhysicalMemory
Get-PSDrive C | Select-Object Used, Free
Get-NetTCPConnection -State Listen | Sort-Object LocalPort | Select-Object LocalAddress, LocalPort, OwningProcess -Unique
Import-Module WebAdministration -ErrorAction SilentlyContinue; Get-Website | Select-Object Name, State, Bindings
```
- Needs about **3.5 GB** on C: (plus room for logs) and 2 GB free RAM. Port 7071 must be free.
- On Azure VMs **D: is the temporary disk** (contents can be lost) – never install there.
- Note any existing Python or Tesseract installs: they must stay untouched.
- Save a before-snapshot (machine/user `PATH` and `TESSDATA_PREFIX`, existing `tesseract --version`, file hashes, uninstall registry entries) to compare at the end.

## 2. Get the code
```powershell
New-Item -ItemType Directory -Force C:\ezocr, C:\ezocr\tmp, C:\ezocr\logs | Out-Null
git clone https://github.com/tech-ezofis-git/ez-ocr.git C:\ezocr\app
```

## 3. Python 3.11.9 (NuGet zip) and packages
```powershell
Invoke-WebRequest https://www.nuget.org/api/v2/package/python/3.11.9 -OutFile $env:TEMP\python.zip
Expand-Archive $env:TEMP\python.zip $env:TEMP\python-nuget -Force
Copy-Item $env:TEMP\python-nuget\tools C:\Python311 -Recurse

C:\Python311\python.exe -m venv C:\ezocr\venv
$py = 'C:\ezocr\venv\Scripts\python.exe'
& $py -m pip install --no-cache-dir --upgrade pip
& $py -m pip install --no-cache-dir "numpy<2"
& $py -m pip install --no-cache-dir paddlepaddle==2.6.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
& $py -m pip install --no-cache-dir -r C:\ezocr\app\requirements.txt
& $py -m pip install --no-cache-dir "protobuf<=3.20.2"
$env:PLAYWRIGHT_BROWSERS_PATH = 'C:\ezocr\ms-playwright'; & $py -m playwright install chromium
```
Paddle needs the Microsoft Visual C++ 2015–2022 x64 runtime.

## 4. PaddleOCR models
```powershell
Set-Location C:\ezocr\app
C:\ezocr\venv\Scripts\python.exe download_paddle_models.py   # creates paddle\det, paddle\rec, paddle\cls
Remove-Item -Recurse -Force $env:USERPROFILE\.paddleocr      # PaddleOCR's own download cache
```

## 5. Tesseract 4.1 in its own folder (extracted, not installed)
1. Get the standalone `7zr.exe` and the 7-Zip x64 setup from 7-zip.org; use `7zr` to unpack the setup into a temp folder (7-Zip itself is **not** installed).
2. Use that `7z.exe` to extract the Tesseract setup into `C:\ezocr\tesseract`; delete the extracted `tesseract-uninstall.exe` so nobody runs it against another installed Tesseract.
3. Put the exact language file in place and check it:
```powershell
$td = 'C:\ezocr\tesseract\tessdata\eng.traineddata'
Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/eng.traineddata -OutFile $td
(Get-FileHash $td -Algorithm SHA256).Hash   # must be 7D4322BD2A7749724879683FC3912CB542F19906C83BCC1A52132556427170B2
& C:\ezocr\tesseract\tesseract.exe --version
```

## 6. Azure Functions Core Tools (portable ZIP)
Download `Azure.Functions.Cli.win-x64.<version>.zip` from the Core Tools GitHub releases, check it against the published `.sha2`, unzip to `C:\ezocr\func`, then:
```powershell
C:\ezocr\func\func.exe --version
```

## 7. Settings (`C:\ezocr\app\local.settings.json`, never committed)
```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "<storage account connection string>",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "languageWorkers__python__defaultExecutablePath": "C:\\ezocr\\venv\\Scripts\\python.exe",
    "AzureWebJobsFeatureFlags": "EnableWorkerIndexing",
    "PADDLE_MODELS_DIR": "C:\\ezocr\\app\\paddle",
    "TESSERACT_HOME": "C:\\ezocr\\tesseract",
    "TESSERACT_CMD": "C:\\ezocr\\tesseract\\tesseract.exe",
    "TESSERACT_TESSDATA_DIR": "C:\\ezocr\\tesseract\\tessdata",
    "TESSERACT_TIMEOUT_SECONDS": "30",
    "OMP_THREAD_LIMIT": "1",
    "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION": "python",
    "FLAGS_use_mkldnn": "0",
    "FLAGS_use_onednn": "0",
    "OCR_TMP_DIR": "C:\\ezocr\\tmp",
    "PLAYWRIGHT_BROWSERS_PATH": "C:\\ezocr\\ms-playwright",
    "PYTHONDONTWRITEBYTECODE": "1"
  }
}
```
- `languageWorkers__python__defaultExecutablePath` is **required**: without it the host may start another Python found on the machine.
- `OCR_TMP_DIR` must be **outside** `C:\ezocr\app`, and `PYTHONDONTWRITEBYTECODE=1` stops `__pycache__` being written there: the Functions host restarts when files in the app folder change.
- `PLAYWRIGHT_BROWSERS_PATH` lets a service account find Chromium.
- An empty `AzureWebJobsStorage` works for HTTP functions in a quick test, but the host then logs "Unhealthy" every 30 s – set a real connection string before running as a service.

## 8. Run and test on localhost only
```powershell
Set-Location C:\ezocr\app
C:\ezocr\func\func.exe start --port 7071
```
`func` listens on **127.0.0.1:7071 only**. In a second window (use `127.0.0.1`, not `localhost` – `localhost` tries IPv6 first and adds ~1 s):
```powershell
curl.exe http://127.0.0.1:7071/api/health
curl.exe -F file=@sample.pdf -F engine=tesseract -F page_no=1 http://127.0.0.1:7071/api/extract_text
curl.exe -F file=@sample.pdf -F engine=paddle -F page_no=1 http://127.0.0.1:7071/api/extract_text
curl.exe -F file=@mail.eml -o mail.pdf http://127.0.0.1:7071/api/emltopdfconvert
```
- Use **synthetic** test files; send the same files to the current production endpoint and compare the text.
- Run **8–16 calls at once** with PDFs (this found the PDFium thread-safety bug).
- Compare the before/after snapshot from step 1: it must be identical.

### Run as a Windows service (NSSM, portable exe)
Use a dedicated low-privilege local account: read access to `C:\ezocr`, write access to `C:\ezocr\tmp` and `C:\ezocr\logs`.
```powershell
nssm install ezocr C:\ezocr\func\func.exe "start --port 7071"
nssm set ezocr AppDirectory C:\ezocr\app
nssm set ezocr ObjectName .\<service-account> <password>
nssm set ezocr AppStdout C:\ezocr\logs\out.log
nssm set ezocr AppStderr C:\ezocr\logs\err.log
nssm set ezocr AppRotateFiles 1
nssm set ezocr AppRotateOnline 1
nssm set ezocr AppRotateBytes 10485760
nssm set ezocr Start SERVICE_AUTO_START
nssm start ezocr
```
Log rotation matters on servers with little free disk. Port 7071 stays closed to the outside.

## 9. Publish through IIS (changes IIS – do it in a quiet window)
1. URL Rewrite and Application Request Routing (ARR) must be installed; in ARR server settings enable the proxy and set the timeout to 120 s.
2. Create a **new site** bound only to the new host name (for example `ocr.<domain>`) with an HTTPS certificate. Do not edit existing sites.
3. Reverse-proxy rule on that site: `(.*)` → `http://127.0.0.1:7071/{R:1}`.
4. Request limits: `maxAllowedContentLength` (for example 52428800 = 50 MB).
5. **IP restrictions** on the site: allow only the calling apps' outbound IPs; deny everything else.

## Roll back / remove
```powershell
nssm stop ezocr; nssm remove ezocr confirm
```
Then delete the IIS site, `C:\ezocr` and `C:\Python311`. Callers keep using the container endpoint until they are switched.
