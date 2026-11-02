# Run ez-ocr on Windows Server (no Docker)

Runs the **same code** as the container (`function_app.py`, `ocr.py`) directly on Windows Server 2019 or newer, as a Windows service behind IIS.
Windows Server 2012 R2 is **not** supported (no Chromium for EML → PDF, no security updates).

Everything goes into `C:\ezocr` and `C:\Python311`. **No installers are run**, so existing Python, Tesseract and IIS apps on a shared server are not touched.
Run every step in an **elevated PowerShell**. Nothing here touches existing IIS sites until step 11.

Changes made outside these two folders (all reversible, see *Roll back*):
- a local account `svc-ezocr` with the "Log on as a service" right (step 9),
- the Windows service `ezocr` (step 10),
- an IIS site (step 11).

## Versions (must match the container, so OCR output is the same)
| Item | Version | Source |
|---|---|---|
| Python | 3.11.9 x64 | NuGet package `python` 3.11.9, unzipped (no installer, registry or PATH change) |
| Azure Functions Core Tools | 4.x (tested 4.16.0) | portable ZIP `Azure.Functions.Cli.win-x64.<version>.zip` (GitHub releases), unzipped to `C:\ezocr\func` |
| Tesseract | 4.1.0 (`elag2019`) | UB Mannheim `tesseract-ocr-w64-setup-v4.1.0-elag2019.exe`, **extracted with 7-Zip, never run**. No Windows build of 4.1.1 (the container's version) exists; output was compared and matches |
| `eng.traineddata` | `tessdata_fast` tag **4.1.0** | SHA-256 `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`, identical to the container's file |
| Python packages | `requirements.txt` | plus `paddlepaddle==2.6.1` from the Paddle CPU index and `protobuf<=3.20.2` |
| PaddleOCR models | 2.x format in `paddle\` | from `download_paddle_models.py` |
| Service wrapper | WinSW **2.12.0** (`WinSW-x64.exe`) | GitHub `winsw/winsw` release v2.12.0, portable exe (see step 10 for why not NSSM) |

## 1. Check the server first (read-only)
```powershell
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, FreePhysicalMemory
Get-PSDrive C | Select-Object Used, Free
Get-NetTCPConnection -State Listen | Sort-Object LocalPort | Select-Object LocalAddress, LocalPort, OwningProcess -Unique
Import-Module WebAdministration -ErrorAction SilentlyContinue; Get-Website | Select-Object Name, State, Bindings
Get-LocalUser svc-ezocr -ErrorAction SilentlyContinue; Get-Service ezocr -ErrorAction SilentlyContinue   # both must not exist yet
net accounts | Select-String 'Maximum password age'                                                    # see step 9
icacls C:\ | Select-String 'Authenticated Users'                                                       # see step 8
```
- Needs about **3.5 GB** on C: plus room for logs (service logs are capped at about 240 MB, step 10) and 2 GB free RAM. Port 7071 must be free.
- On Azure VMs **D: is the temporary disk** (contents can be lost), so never install there.
- Note any existing Python or Tesseract installs: they must stay untouched.
- Save a before-snapshot (machine/user `PATH` and `TESSDATA_PREFIX`, existing `tesseract --version`, file hashes, uninstall registry entries) to compare at the end.

## 2. Get the code
```powershell
New-Item -ItemType Directory -Force C:\ezocr, C:\ezocr\tmp, C:\ezocr\logs, C:\ezocr\tools | Out-Null
git clone https://github.com/tech-ezofis-git/ez-ocr.git C:\ezocr\app
```
Keep `C:\ezocr\app` a clean checkout (only the gitignored `local.settings.json` and `paddle\` in it) so `git pull` works for updates. Keep notes, tests and logs outside it.

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
- Paddle needs the Microsoft Visual C++ 2015–2022 x64 runtime.
- Don't use the python.org installer: on a server that already has a Python 3.11.x, it treats that install as an upgrade target.

## 4. PaddleOCR models
```powershell
Set-Location C:\ezocr\app
C:\ezocr\venv\Scripts\python.exe download_paddle_models.py   # creates paddle\det, paddle\rec, paddle\cls
Remove-Item -Recurse -Force $env:USERPROFILE\.paddleocr      # PaddleOCR's own download cache
```

## 5. Tesseract 4.1 in its own folder (extracted, not installed)
1. Get the standalone `7zr.exe` and the 7-Zip x64 setup from 7-zip.org, and use `7zr` to unpack the setup into a temp folder. 7-Zip itself is **not** installed; delete it afterwards.
2. Use that `7z.exe` to extract the Tesseract setup into `C:\ezocr\tesseract`. Delete the extracted `tesseract-uninstall.exe` so nobody runs it against another installed Tesseract.
3. Put the exact language file in place and check it:
```powershell
$td = 'C:\ezocr\tesseract\tessdata\eng.traineddata'
Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/eng.traineddata -OutFile $td
(Get-FileHash $td -Algorithm SHA256).Hash   # must be 7D4322BD2A7749724879683FC3912CB542F19906C83BCC1A52132556427170B2
& C:\ezocr\tesseract\tesseract.exe --version
```
Don't put it in `C:\Program Files\Tesseract-OCR`: another app on the server may already use a different Tesseract there.

## 6. Azure Functions Core Tools (portable ZIP)
Download `Azure.Functions.Cli.win-x64.<version>.zip` from the Core Tools GitHub releases, check it against the published `.sha2`, and unzip to `C:\ezocr\func`. Don't use the MSI. Then:
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
  },
  "ConnectionStrings": {}
}
```
- `languageWorkers__python__defaultExecutablePath` is useful but **not sufficient** on its own: Core Tools may still start another Python found on the machine. The worker then fails with `No module named 'playwright'`. Also set the colon form `languageWorkers:python:defaultExecutablePath` in the **process environment** (service XML in step 10a, or `${env:...}` for a foreground test, step 12).
- `OCR_TMP_DIR` must be **outside** `C:\ezocr\app`, and `PYTHONDONTWRITEBYTECODE=1` stops `__pycache__` being written there. The Functions host restarts when files in the app folder change.
- `PLAYWRIGHT_BROWSERS_PATH` lets the service account find Chromium.

### Storage account (`AzureWebJobsStorage`)
- An empty value works for a quick HTTP test, but the host then logs `Process reporting unhealthy … azure.functions.webjobs.storage … Unable to create client for AzureWebJobsStorage` every 30 s. Set a real connection string **before** running as a service.
- Use a **dedicated** storage account for this server, in the server's region: StorageV2, Standard LRS, minimum TLS 1.2, no anonymous blob access.
  - Don't share the account with the container app. The Functions host keeps its host lock and keys there (containers `azure-webjobs-hosts`, `azure-webjobs-secrets`), and two hosts on one account can collide.
- **The connection string goes only into `local.settings.json`**: never into the service XML, scripts, docs or chat. Let an admin paste it in with Notepad from an elevated session.
  - After step 8, the file can only be opened elevated.
- Check it without printing the key:
```powershell
$v = (Get-Content C:\ezocr\app\local.settings.json -Raw | ConvertFrom-Json).Values.AzureWebJobsStorage
"set: $(-not [string]::IsNullOrWhiteSpace($v)); https: $($v -match 'DefaultEndpointsProtocol=https'); key: $($v -match 'AccountKey='); stray spaces: $($v -ne $v.Trim())"
Remove-Variable v
git -C C:\ezocr\app check-ignore local.settings.json   # must print the file name
```
- When the service runs with a working value, the log shows `Host lock lease acquired by instance ID …` a few seconds after start, and no "Unhealthy" lines.

## 8. Harden folder permissions
By default `C:\ezocr` and `C:\Python311` inherit `Authenticated Users: Modify` from `C:\`. Any local or service account (including IIS app-pool identities) could then change the code, venv or Tesseract, and read the storage key. Turn inheritance off and grant explicitly.

Run this **after** creating `svc-ezocr` (step 9a). Well-known SIDs are used so it works on any OS language: `*S-1-5-18` = SYSTEM, `*S-1-5-32-544` = Administrators.
```powershell
# backups of the previous top-level ACLs
New-Item -ItemType Directory -Force C:\ezocr\acl-backup | Out-Null
icacls C:\ezocr      /save C:\ezocr\acl-backup\ezocr-top.acl
icacls C:\Python311  /save C:\ezocr\acl-backup\python311-top.acl

foreach ($d in 'C:\ezocr', 'C:\Python311') {
  icacls $d /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' 'svc-ezocr:(OI)(CI)RX'
  # reset only the contents ("$d\*"): /reset on the folder itself could turn inheritance from C:\ back on
  icacls "$d\*" /reset /T /C /Q      # children inherit from the new top ACL (removes stray explicit ACEs)
}
icacls C:\ezocr | Select-String 'Authenticated Users'   # must print nothing (same for C:\Python311)
foreach ($d in 'C:\ezocr\tmp', 'C:\ezocr\logs') { icacls $d /grant 'svc-ezocr:(OI)(CI)M' }

# the settings file holds the storage key
icacls C:\ezocr\app\local.settings.json /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' 'svc-ezocr:R'
```
| Path | SYSTEM | Administrators | `svc-ezocr` | Everyone else |
|---|---|---|---|---|
| `C:\ezocr`, `C:\Python311` | Full | Full | Read & execute | none |
| `C:\ezocr\tmp`, `C:\ezocr\logs` (incl. `logs\service`) | Full | Full | Modify | none |
| `C:\ezocr\app\local.settings.json` | Full | Full | Read | none |

- Side effect: admins reach `C:\ezocr` only **elevated**, so a non-elevated Explorer window is denied. Run `git pull` from an elevated prompt.
- If git complains about "dubious ownership", add the folder with `git config --global --add safe.directory C:/ezocr/app` for that admin.
- A file copied in from `%TEMP%` keeps its own explicit ACL. Run `icacls <file> /reset` on it (the `/reset /T` above does this once).

## 9. Service account `svc-ezocr`
### 9a. Create it
A local user, member of **Users only**. Its password never expires: with a domain or local policy such as "maximum password age 42 days", an expiring password would stop the service from starting. Nobody needs to know the password, so generate a random one in memory and never store or show it.
```powershell
function New-RandomPassword { $b = New-Object byte[] 30; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b) + 'a1!Z' }

$sec = ConvertTo-SecureString (New-RandomPassword) -AsPlainText -Force
New-LocalUser -Name svc-ezocr -Password $sec -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires `
  -Description 'ez-ocr service account (no interactive use)'          # Description: max 48 characters
Add-LocalGroupMember -Group (Get-LocalGroup -SID S-1-5-32-545) -Member svc-ezocr   # Users
Remove-Variable sec
```

### 9b. Grant "Log on as a service" (secedit)
Only the account's SID is added to `SeServiceLogonRight`; all other entries and rights stay as they are. Back up first and compare afterwards.
```powershell
$sid = (Get-LocalUser svc-ezocr).SID.Value
$bk  = 'C:\ezocr\acl-backup\secpol-before-service.inf'
secedit /export /cfg $bk /areas USER_RIGHTS /quiet
$line = (Select-String -Path $bk -Pattern '^SeServiceLogonRight').Line
$old  = ($line -split '=', 2)[1].Trim() -split ','
# exported entries are '*SID' or account names; turn names into SIDs so nothing is dropped
$sids = foreach ($e in $old) { if ($e.StartsWith('*')) { $e.Substring(1) } else { (New-Object Security.Principal.NTAccount($e)).Translate([Security.Principal.SecurityIdentifier]).Value } }
$new  = (($sids + $sid) | Select-Object -Unique | ForEach-Object { "*$_" }) -join ','
$inf  = "$env:TEMP\svclogon.inf"; $sdb = "$env:TEMP\svclogon.sdb"
@"
[Unicode]
Unicode=yes
[Version]
signature="`$CHICAGO`$"
Revision=1
[Privilege Rights]
SeServiceLogonRight = $new
"@ | Set-Content $inf -Encoding Unicode
secedit /configure /db $sdb /cfg $inf /areas USER_RIGHTS /quiet
Remove-Item $inf, $sdb, "$env:TEMP\svclogon.jfm" -ErrorAction SilentlyContinue
```
Check: export again and confirm that `SeServiceLogonRight` has exactly one more entry, that all other `Se*` lines are identical, and that no `SeDeny*Logon*` right lists the account.

## 10. Run as a Windows service (WinSW)
Why WinSW and not NSSM:
- nssm.cc, the only official NSSM source, was unreachable (HTTP 503) when this was set up.
- NSSM rotates logs but never deletes old ones.
- WinSW is a single portable exe with an XML config next to it. It rotates **and** deletes old logs, and supports delayed auto start and restart-on-failure.

### 10a. WinSW exe and config
1. Download `WinSW-x64.exe` from the GitHub release `winsw/winsw` **v2.12.0**.
   - Check the size against the GitHub API (`assets[].size`) and record the SHA-256.
   - WinSW 2.x releases are not Authenticode-signed, and GitHub publishes no checksum.
2. Save it as `C:\ezocr\tools\ezocr-service.exe`. WinSW reads the XML with the same base name.
3. Run `icacls C:\ezocr\tools\ezocr-service.exe /reset`, so the file drops the ACL it brought from the download folder.

`C:\ezocr\tools\ezocr-service.xml` holds **no secrets**. The account and password are set on the service itself (step 10b), never in this file.
```xml
<service>
  <id>ezocr</id>
  <name>ez-ocr OCR API</name>
  <description>ez-ocr Azure Functions host (Tesseract/Paddle OCR, EML to PDF) on 127.0.0.1:7071. Files: C:\ezocr</description>

  <executable>C:\ezocr\func\func.exe</executable>
  <arguments>start --port 7071</arguments>
  <workingdirectory>C:\ezocr\app</workingdirectory>

  <env name="PATH" value="C:\ezocr\venv\Scripts;C:\ezocr\func;C:\Windows\System32;C:\Windows;C:\Windows\System32\WindowsPowerShell\v1.0" />
  <!-- REQUIRED: forces the venv's Python. Without it Core Tools may pick another Python (e.g. via the py launcher). -->
  <env name="languageWorkers:python:defaultExecutablePath" value="C:\ezocr\venv\Scripts\python.exe" />
  <env name="FUNCTIONS_CORE_TOOLS_TELEMETRY_OPTOUT" value="1" />

  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>

  <onfailure action="restart" delay="10 sec" />
  <onfailure action="restart" delay="30 sec" />
  <onfailure action="restart" delay="60 sec" />
  <resetfailure>1 hour</resetfailure>

  <stoptimeout>30 sec</stoptimeout>
  <stopparentprocessfirst>true</stopparentprocessfirst>

  <!-- 10 MB per file (sizeThreshold is in KB), keep the newest 8 per stream: at most about 240 MB -->
  <logpath>C:\ezocr\logs\service</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
</service>
```
- `C:\ezocr\tools\ezocr-service.exe status` should print `NonExistent`, which means the config is valid and nothing is installed yet.
- Logs:
  - `logs\service\ezocr-service.out.log` and `.err.log` are rolled at 10 MB, keeping 8 each.
  - `ezocr-service.wrapper.log` has only start/stop lines and is **not** rolled by WinSW 2.x. It grows by a few hundred bytes per start.
- PATH is set only for the service process; machine and user PATH stay unchanged.
- **`languageWorkers:python:defaultExecutablePath` (colon form) is required** in the service environment. Core Tools chooses the Python interpreter with its own probe, and the result is **not stable**: on a server that also has a system Python (and the `py` launcher), a restart can pick that Python even with the venv first on PATH and the double-underscore setting in `local.settings.json`. The worker then fails on `import playwright`, no functions are indexed, and **every call returns 404**. Check after each start: the log shows `Found Python version 3.11.9 (C:\ezocr\venv\Scripts\python.exe)`.

### 10b. Install, set the account, start
```powershell
New-Item -ItemType Directory -Force C:\ezocr\logs\service | Out-Null
C:\ezocr\tools\ezocr-service.exe install

# fresh random password: set on the account and handed to the service, in memory only
$pw = New-RandomPassword                                   # function from step 9a
Set-LocalUser -Name svc-ezocr -Password (ConvertTo-SecureString $pw -AsPlainText -Force)
$svc = Get-CimInstance Win32_Service -Filter "Name='ezocr'"
(Invoke-CimMethod -InputObject $svc -MethodName Change -Arguments @{ StartName = '.\svc-ezocr'; StartPassword = $pw }).ReturnValue   # 0 = OK
Remove-Variable pw

Get-CimInstance Win32_Service -Filter "Name='ezocr'" | Select-Object StartName, StartMode, DelayedAutoStart   # .\svc-ezocr, Auto, True
sc.exe qfailure ezocr                                      # restart 10 s / 30 s / 60 s, reset 3600 s
Start-Service ezocr
```
- Don't use `sc.exe config … password=`, which puts the password on a command line (visible in process lists and logs). `Win32_Service.Change` keeps it in memory.
- If the password is ever needed again, for example after a policy reset, set a **new** one the same way. There is nothing to look up.

### 10c. Test the service (127.0.0.1:7071)
Use `127.0.0.1`, not `localhost`: `localhost` tries IPv6 first, which adds about 1 s per call.
```powershell
# wait for health (normally 4–6 s after start)
for ($i = 0; $i -lt 60; $i++) { try { Invoke-WebRequest http://127.0.0.1:7071/api/health -UseBasicParsing -TimeoutSec 5; break } catch { Start-Sleep 2 } }

curl.exe -F file=@sample.pdf -F engine=tesseract -F page_no=1 http://127.0.0.1:7071/api/extract_text
curl.exe -F file=@sample.pdf -F engine=paddle    -F page_no=1 http://127.0.0.1:7071/api/extract_text   # first call loads the model (~10 s)
curl.exe -F file=@mail.eml -o mail.pdf http://127.0.0.1:7071/api/emltopdfconvert                       # proves Chromium works under the account

# runs as the service account, listens on loopback only
Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'func.exe', 'ezocr-service.exe' -or $_.CommandLine -like '*ezocr\func\workers*' } |
  ForEach-Object { $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner; '{0} {1}\{2}' -f $_.Name, $o.Domain, $o.User }   # all .\svc-ezocr
Get-NetTCPConnection -LocalPort 7071 -State Listen | Select-Object LocalAddress   # 127.0.0.1 only

# storage OK, no errors
Select-String C:\ezocr\logs\service\*.log -Pattern 'Unhealthy','denied','Exception'   # nothing
Select-String C:\ezocr\logs\service\ezocr-service.out.log -Pattern 'Host lock lease acquired'
```
Then **restart and test again**: `Restart-Service ezocr`, wait for health, and repeat the calls. The text must be identical to the first run and to the foreground test from step 12.

Expected side effects, all harmless:
- The account's profile `C:\Users\svc-ezocr` is created at first start.
- It contains empty `.azurefunctions` and `.cache\paddle` folders.
- `C:\ezocr\tmp` stays empty between calls.
- No `__pycache__` appears in the app folder.

### 10d. Operating it
- **After every code or settings update, restart the service** (`Restart-Service ezocr`). The Functions host notices file changes but can keep the old Python worker with the old code loaded.
- Update the code: `git -C C:\ezocr\app pull` (elevated), then `Restart-Service ezocr`, then the tests from 10c.
- Update a Python package: **stop → install → start**, not install → restart. The running worker keeps the packages' `.pyd` files open, so they can't be replaced while it runs.
  1. Download the exact wheel first, check its SHA-256 against PyPI, and also keep the **current** version's wheel for rollback.
  2. Test the new version in a **copy of the venv** on another port (see step 12) before touching the service.
  3. In a quiet moment: `Stop-Service ezocr`, `pip install --no-deps --no-index <wheel>`, `pip check`, `Start-Service ezocr`, then the tests from 10c. If anything fails, install the old wheel the same way.
- Never take library upgrades for the pinned OCR stack (Paddle, OpenCV, numpy, protobuf, Playwright) from automated PRs without the tests above; see `.github/dependabot.yml`.
- Status: `Get-Service ezocr` or `C:\ezocr\tools\ezocr-service.exe status`. Logs: `C:\ezocr\logs\service`.
- Port 7071 stays closed to the outside; no firewall rule is needed for it.

## 11. Publish through IIS (changes IIS: do it in a quiet window)
Facts to check first (read-only):
```powershell
Get-WebGlobalModule | Where-Object Name -match 'Rewrite|ApplicationRequestRouting'   # URL Rewrite + ARR installed?
Get-WebConfiguration system.webServer/proxy | Select-Object enabled, timeout         # ARR proxy on? timeout?
Get-WebBinding | Where-Object protocol -eq 'https' | Select-Object ItemXPath, bindingInformation, sslFlags
netsh http show sslcert                                                             # which cert is on which binding
Get-ChildItem Cert:\LocalMachine\My, Cert:\LocalMachine\WebHosting | Select-Object Subject, NotAfter, Thumbprint
```
1. **URL Rewrite and ARR** must be installed.
   - The ARR server proxy setting (`system.webServer/proxy enabled`, timeout **120 s**) is **server-wide**.
   - On a shared server it may **already be enabled** for other sites. If it's already on with a timeout of at least 120 s, leave it alone. Changing it affects every site.
2. **Create a new site** with its own app pool ("No Managed Code", ApplicationPoolIdentity) and an empty folder (e.g. `C:\ezocr\iis`).
   - Bind it **only** to the new host name (e.g. `ocr.<domain>`) on 443, with **SNI** (`sslFlags=1`).
   - Existing sites often have a catch-all `*:443` binding. A host-specific SNI binding takes precedence for its own name and doesn't change the catch-all.
   - Don't edit existing sites.
3. **Certificate:** it must cover the new host name and must not be expired; check `NotAfter`. A wildcard already in the store may have expired. Import the PFX into `LocalMachine\My` (or `WebHosting`).
4. **DNS:** an A/CNAME record for the host name pointing to the server's public IP. Port 443 must be open in the cloud network rules and Windows Firewall; it usually already is when other sites use HTTPS.
5. **Reverse-proxy rule** on that site, to **`127.0.0.1`** (not `localhost`, which costs about 1 s per call via IPv6 first; `func` listens on IPv4 loopback only). The site's `web.config`:
```xml
<configuration>
  <system.webServer>
    <rewrite>
      <rules>
        <rule name="ezocr" stopProcessing="true">
          <match url="(.*)" />
          <action type="Rewrite" url="http://127.0.0.1:7071/{R:1}" />
        </rule>
      </rules>
    </rewrite>
    <security>
      <requestFiltering>
        <!-- IIS default is 30000000 bytes (~30 MB); larger uploads get 404.13. Set to the largest PDF/EML callers send. -->
        <requestLimits maxAllowedContentLength="52428800" />
      </requestFiltering>
    </security>
  </system.webServer>
</configuration>
```
6. **Upload limit:** IIS request filtering allows **30 MB by default** (`maxAllowedContentLength` = 30000000). Raise it only as far as needed. Large PDFs also cost memory and CPU.
7. **Access:** the functions are anonymous.
   - Add **IP restrictions** on the site: allow only the calling apps' outbound IPs and deny everything else. This needs the IIS "IP and Domain Restrictions" feature; check whether it's installed.
   - Or decide to stay open like the container.
8. **Test** through `https://<host>/api/health` and the OCR/EML calls, compare with the container endpoint, then switch the callers. Keep the container running until the server has run cleanly.

## 12. Foreground test (before the service, or for debugging)
Stop the service first (`Stop-Service ezocr`) if it exists, since only one host can use port 7071.
```powershell
Set-Location C:\ezocr\app
${env:languageWorkers:python:defaultExecutablePath} = 'C:\ezocr\venv\Scripts\python.exe'   # same reason as in the service XML
C:\ezocr\func\func.exe start --port 7071
```
`func` listens on **127.0.0.1:7071 only**. Test from a second window as in 10c.
To test another venv (e.g. a package upgrade) without stopping the service, use a copy of the code and venv and another port (`--port 7072`), with this variable pointing at that venv's `python.exe`.
- Use **synthetic** test files; send the same files to the current production endpoint and compare the text.
- Run **8 and 16 calls at once** with PDFs. This found the PDFium thread-safety bug, now fixed with `_PDFIUM_LOCK`.
  - With the PDFium and Paddle locks, 16 parallel PDF calls take about 8 s at the client, far below the 120 s ARR timeout.
- EML: test with a hostile email (img, stylesheet, CSS background, iframe, script fetch, `<link rel=prefetch>`, `<link rel=preconnect>` …) against a local listener such as `python -m http.server 8099 --bind 127.0.0.1` serving an empty folder. **Zero** requests must arrive. A normal email with an inline `cid:` image must still render.
- Compare the before/after snapshot from step 1: it must be identical.
- Remove the empty `~\.azurefunctions` and `~\.cache\paddle` folders that runs as the admin leave in that admin's profile.

## Roll back / remove
```powershell
Stop-Service ezocr
C:\ezocr\tools\ezocr-service.exe uninstall

# "Log on as a service": restore the exported user rights
# (only if no other user-rights change happened since the backup; otherwise remove just the svc-ezocr SID as in step 9b)
secedit /configure /db $env:TEMP\restore.sdb /cfg C:\ezocr\acl-backup\secpol-before-service.inf /areas USER_RIGHTS /quiet

Get-CimInstance Win32_UserProfile | Where-Object LocalPath -like '*\svc-ezocr' | Remove-CimInstance
Remove-LocalUser svc-ezocr

# permissions back to inherited from C:\ (or restore the saved top-level ACLs with: icacls C:\ /restore C:\ezocr\acl-backup\<file>.acl)
icacls C:\ezocr     /reset /T /C /Q
icacls C:\Python311 /reset /T /C /Q
```
Then delete the IIS site and its app pool, `C:\ezocr` and `C:\Python311`. Remove the storage account if it isn't used elsewhere. Callers keep using the container endpoint until they are switched.
