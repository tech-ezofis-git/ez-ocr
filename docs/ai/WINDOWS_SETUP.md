# Run ez-ocr on Windows Server (no Docker)

Runs the **same code** as the container (`function_app.py`, `ocr.py`) directly on Windows Server 2019 or newer, as a Windows service behind IIS.
Windows Server 2012 R2 is **not** supported (no Chromium for EML → PDF, no security updates).

Run every step in an **elevated PowerShell**. Nothing here touches existing IIS sites until step 9.

## Versions (must match the container, so OCR output is the same)
| Item | Version | Notes |
|---|---|---|
| Python | 3.11.9 x64 | 3.11 is required by the pinned Paddle stack |
| Azure Functions Core Tools | 4.x | provides the `func` host (needs no separate .NET install) |
| Tesseract | **4.1.x** | container has 4.1.1. Windows builds: UB Mannheim archive. Avoid 5.x unless outputs are compared and accepted |
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
Needs about 4 GB free disk and 2 GB free RAM. Port 7071 must be free.

## 2. Get the code
```powershell
New-Item -ItemType Directory -Force C:\ezocr | Out-Null
git clone https://github.com/tech-ezofis-git/ez-ocr.git C:\ezocr\app
```

## 3. Python 3.11.9 and packages
```powershell
$py = "$env:TEMP\python-3.11.9-amd64.exe"
Invoke-WebRequest https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe -OutFile $py
Start-Process $py -Wait -ArgumentList '/quiet InstallAllUsers=1 PrependPath=0 Include_test=0 TargetDir=C:\Python311'

C:\Python311\python.exe -m venv C:\ezocr\venv
$pip = 'C:\ezocr\venv\Scripts\python.exe -m pip'
Invoke-Expression "$pip install --upgrade pip"
Invoke-Expression "$pip install `"numpy<2`""
Invoke-Expression "$pip install paddlepaddle==2.6.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/"
Invoke-Expression "$pip install -r C:\ezocr\app\requirements.txt"
Invoke-Expression "$pip install `"protobuf<=3.20.2`""
C:\ezocr\venv\Scripts\python.exe -m playwright install chromium
```
Paddle needs the Microsoft Visual C++ 2015–2022 x64 runtime; install it if `import paddle` fails.

## 4. PaddleOCR models
```powershell
Set-Location C:\ezocr\app
C:\ezocr\venv\Scripts\python.exe download_paddle_models.py   # creates paddle\det, paddle\rec, paddle\cls
```

## 5. Tesseract 4.1 and the exact language file
Install Tesseract 4.1.x for Windows (64-bit) to `C:\Program Files\Tesseract-OCR`, then replace its English data with the container's exact file:
```powershell
$td = 'C:\Program Files\Tesseract-OCR\tessdata\eng.traineddata'
Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/eng.traineddata -OutFile $td
(Get-FileHash $td -Algorithm SHA256).Hash   # must be 7D4322BD2A7749724879683FC3912CB542F19906C83BCC1A52132556427170B2
& 'C:\Program Files\Tesseract-OCR\tesseract.exe' --version
```

## 6. Azure Functions Core Tools
Install Core Tools v4 (x64 MSI from Microsoft), then check:
```powershell
func --version
```

## 7. Settings (`C:\ezocr\app\local.settings.json`, never committed)
```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "<storage account connection string>",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "AzureWebJobsFeatureFlags": "EnableWorkerIndexing",
    "PADDLE_MODELS_DIR": "C:\\ezocr\\app\\paddle",
    "TESSERACT_CMD": "C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
    "TESSERACT_TIMEOUT_SECONDS": "30",
    "OMP_THREAD_LIMIT": "1",
    "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION": "python",
    "FLAGS_use_mkldnn": "0",
    "FLAGS_use_onednn": "0",
    "OCR_TMP_DIR": "C:\\ezocr\\tmp"
  }
}
```
`OCR_TMP_DIR` must be **outside** `C:\ezocr\app`: the Functions host restarts when files in the app folder change.

## 8. Run and test on localhost only
```powershell
Set-Location C:\ezocr\app
$env:PATH = "C:\ezocr\venv\Scripts;$env:PATH"
func start --port 7071
```
In a second window:
```powershell
curl.exe http://localhost:7071/api/health
curl.exe -F file=@sample.pdf -F engine=tesseract -F page_no=1 http://localhost:7071/api/extract_text
curl.exe -F file=@sample.pdf -F engine=paddle -F page_no=1 http://localhost:7071/api/extract_text
curl.exe -F file=@mail.eml -o mail.pdf http://localhost:7071/api/emltopdfconvert
```
Before going further, send the **same documents** to the current production endpoint and compare the text (`Compare-Object`, or a diff tool). Also run 3–8 calls at once to check parallel requests.

### Run as a Windows service (NSSM)
```powershell
nssm install ezocr "C:\Program Files\Microsoft\Azure Functions Core Tools\func.exe" "start --port 7071"
nssm set ezocr AppDirectory C:\ezocr\app
nssm set ezocr AppEnvironmentExtra "PATH=C:\ezocr\venv\Scripts;C:\Windows\System32;C:\Windows"
nssm set ezocr AppStdout C:\ezocr\logs\out.log
nssm set ezocr AppStderr C:\ezocr\logs\err.log
nssm set ezocr Start SERVICE_AUTO_START
New-Item -ItemType Directory -Force C:\ezocr\logs, C:\ezocr\tmp | Out-Null
nssm start ezocr
```
Port 7071 stays closed to the outside (Windows Firewall and the Azure network rules do not allow it).

## 9. Publish through IIS (changes IIS – do it in a quiet window)
1. Install **URL Rewrite** and **Application Request Routing (ARR)**; in ARR server settings enable the proxy and set the timeout to 120 s.
2. Create a **new site** bound only to the new host name (for example `ocr.<domain>`) with an HTTPS certificate. Do not edit existing sites.
3. Reverse-proxy rule on that site: `(.*)` → `http://localhost:7071/{R:1}`.
4. Request limits: `maxAllowedContentLength` (for example 52428800 = 50 MB).
5. **IP restrictions** on the site: allow only the calling apps' outbound IPs; deny everything else.

## Roll back / remove
```powershell
nssm stop ezocr; nssm remove ezocr confirm
```
Then delete the IIS site and `C:\ezocr`. Callers keep using the container endpoint until they are switched.
