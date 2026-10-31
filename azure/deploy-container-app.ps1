# Deploy OCR+EML API to Azure Container Apps (Docker Hub image)
# Prerequisites: az login, az extension add --name containerapp

param(
    [string]$ResourceGroup = "rg-ezc-api",
    [string]$Location = "southindia",
    [string]$EnvironmentName = "cae-ocr-eml",
    [string]$AppName = "ez-container-app",
    [string]$AcrName = "",              # auto-generated if empty
    [string]$AcrRepo = "ocr-app",
    [string]$AcrTag = "v2",
    [string]$StorageAccountName = "",   # auto-generated if empty
    [string]$SourceImage = "docker.io/arasuezofis/ocr-app:v2",
    [double]$Cpu = 2.0,
    [string]$Memory = "4Gi",
    [int]$MinReplicas = 1,
    [int]$MaxReplicas = 3
)

$ErrorActionPreference = "Stop"

function Az-Succeeded {
    return ($LASTEXITCODE -eq 0)
}

function Az-Exists {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$AzArgs
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & az @AzArgs 1>$null 2>$null
        return (Az-Succeeded)
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Az-GetTsv {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$AzArgs
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $out = & az @AzArgs -o tsv 2>$null
        if (-not (Az-Succeeded)) { return "" }
        return ($out | Out-String).Trim()
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Sanitize-StorageAccountName {
    param([string]$Name)
    if (-not $Name) { return "" }
    $n = $Name.ToLower()
    $n = ($n -replace "[^a-z0-9]", "")
    if ($n.Length -gt 24) { $n = $n.Substring(0, 24) }
    return $n
}

function Sanitize-AcrName {
    param([string]$Name)
    if (-not $Name) { return "" }
    $n = $Name.ToLower()
    $n = ($n -replace "[^a-z0-9]", "")
    if ($n.Length -gt 50) { $n = $n.Substring(0, 50) }
    return $n
}

function Ensure-ProviderRegistered {
    param([string]$Namespace)
    $state = az provider show -n $Namespace --query "registrationState" -o tsv 2>$null
    if ($state -ne "Registered") {
        Write-Host "Registering provider $Namespace ..."
        az provider register -n $Namespace | Out-Null
    }
}

Write-Host "Checking Azure CLI ..."
az account show | Out-Null
if (-not (Az-Succeeded)) { throw "Azure CLI not logged in. Run: az login" }

$ext = az extension list --query "[?name=='containerapp'].name" -o tsv
if (-not $ext) {
    Write-Host "Installing containerapp extension ..."
    az extension add --name containerapp --upgrade | Out-Null
}

Ensure-ProviderRegistered "Microsoft.App"
Ensure-ProviderRegistered "Microsoft.OperationalInsights"
Ensure-ProviderRegistered "Microsoft.Storage"

Write-Host "Creating resource group $ResourceGroup ($Location) ..."
az group create --name $ResourceGroup --location $Location | Out-Null
if (-not (Az-Succeeded)) { throw "Failed to create resource group: $ResourceGroup" }

$AcrName = Sanitize-AcrName $AcrName
$StorageAccountName = Sanitize-StorageAccountName $StorageAccountName

if (-not $AcrName) {
    $suffix = -join ((48..57) + (97..122) | Get-Random -Count 8 | ForEach-Object { [char]$_ })
    $AcrName = "acrezc$suffix".Substring(0, 20).ToLower()
}

if (-not $StorageAccountName) {
    $suffix = -join ((48..57) + (97..122) | Get-Random -Count 8 | ForEach-Object { [char]$_ })
    $StorageAccountName = "stocremlpdf$suffix".Substring(0, 24).ToLower()
}

if ($StorageAccountName.Length -lt 3) {
    throw "StorageAccountName must be at least 3 characters after sanitization (lowercase letters/numbers only)."
}

if ($AcrName.Length -lt 5) {
    throw "AcrName must be at least 5 characters after sanitization (lowercase letters/numbers only)."
}

Write-Host "Ensuring ACR exists: $AcrName ..."
$acrServer = Az-GetTsv acr show -n $AcrName -g $ResourceGroup --query loginServer
if (-not $acrServer) {
    Write-Host "ACR not found. Creating ACR $AcrName ..."
    az acr create -n $AcrName -g $ResourceGroup --sku Standard --admin-enabled false | Out-Null
    if (-not (Az-Succeeded)) { throw "Failed to create ACR: $AcrName" }

    $acrServer = Az-GetTsv acr show -n $AcrName -g $ResourceGroup --query loginServer
    if (-not $acrServer) { throw "Failed to query ACR login server for: $AcrName" }
}
$acrImage = "$acrServer/$AcrRepo`:$AcrTag"

Write-Host "Importing image into ACR (no Docker needed) ..."
az acr import -n $AcrName --source $SourceImage --image "$AcrRepo`:$AcrTag" --force | Out-Null
if (-not (Az-Succeeded)) {
    throw "Failed to import '$SourceImage' into ACR '$AcrName'. If the Docker Hub image is private, acr import needs credentials."
}

Write-Host "Ensuring storage account exists: $StorageAccountName (AzureWebJobsStorage) ..."
$storageRg = Az-GetTsv storage account show --name $StorageAccountName --query resourceGroup
if (-not $storageRg) {
    az storage account create `
        --name $StorageAccountName `
        --resource-group $ResourceGroup `
        --location $Location `
        --sku Standard_LRS `
        --kind StorageV2 | Out-Null
    if (-not (Az-Succeeded)) {
        # Storage account names are global; if taken, auto-generate a unique one.
        Write-Host "Storage account name '$StorageAccountName' is not available. Generating a new name..."
        $suffix = -join ((48..57) + (97..122) | Get-Random -Count 8 | ForEach-Object { [char]$_ })
        $StorageAccountName = ("ezcst" + $suffix).Substring(0, 24).ToLower()

        az storage account create `
            --name $StorageAccountName `
            --resource-group $ResourceGroup `
            --location $Location `
            --sku Standard_LRS `
            --kind StorageV2 | Out-Null
        if (-not (Az-Succeeded)) { throw "Failed to create storage account: $StorageAccountName" }
    }
    $storageRg = $ResourceGroup
} else {
    Write-Host "Storage account already exists in resource group: $storageRg"
}

$storageConn = az storage account show-connection-string `
    --name $StorageAccountName `
    --resource-group $storageRg `
    --query connectionString -o tsv
if (-not (Az-Succeeded) -or -not $storageConn) { throw "Failed to read storage connection string for: $StorageAccountName" }

$logAnalytics = "${AppName}-logs"
Write-Host "Creating Log Analytics workspace $logAnalytics ..."
az monitor log-analytics workspace create `
    --resource-group $ResourceGroup `
    --workspace-name $logAnalytics `
    --location $Location | Out-Null
if (-not (Az-Succeeded)) { throw "Failed to create Log Analytics workspace: $logAnalytics" }

$logAnalyticsId = az monitor log-analytics workspace show `
    --resource-group $ResourceGroup `
    --workspace-name $logAnalytics `
    --query customerId -o tsv

$logAnalyticsKey = az monitor log-analytics workspace get-shared-keys `
    --resource-group $ResourceGroup `
    --workspace-name $logAnalytics `
    --query primarySharedKey -o tsv

Write-Host "Creating Container Apps environment $EnvironmentName ..."
az containerapp env create `
    --name $EnvironmentName `
    --resource-group $ResourceGroup `
    --location $Location `
    --logs-workspace-id $logAnalyticsId `
    --logs-workspace-key $logAnalyticsKey | Out-Null
if (-not (Az-Succeeded)) { throw "Failed to create Container Apps environment: $EnvironmentName" }

$envVarList = @(
    "AzureWebJobsStorage=secretref:azurewebjobsstorage",
    "FUNCTIONS_WORKER_RUNTIME=python",
    "PYTHON_ENABLE_WORKER_EXTENSIONS=1",
    "AzureWebJobsFeatureFlags=EnableWorkerIndexing",
    "WEBSITES_PORT=80",
    "PADDLE_MODELS_DIR=/home/site/wwwroot/paddle",
    "TESSERACT_CMD=/usr/bin/tesseract"
)

Write-Host "Creating container app $AppName ..."
$appExists = Az-Exists containerapp show --name $AppName --resource-group $ResourceGroup

if (-not $appExists) {
    $createArgs = @(
        "containerapp", "create",
        "--name", $AppName,
        "--resource-group", $ResourceGroup,
        "--environment", $EnvironmentName,
        "--image", $acrImage,
        "--registry-server", $acrServer,
        "--target-port", "80",
        "--ingress", "external",
        "--cpu", "$Cpu",
        "--memory", $Memory,
        "--min-replicas", "$MinReplicas",
        "--max-replicas", "$MaxReplicas",
        "--secrets", "azurewebjobsstorage=$storageConn",
        "--env-vars"
    ) + $envVarList
    az @createArgs | Out-Null
    if (-not (Az-Succeeded)) { throw "Failed to create container app: $AppName" }
} else {
    Write-Host "Container app exists. Updating image/resources ..."
    az containerapp update `
        --name $AppName `
        --resource-group $ResourceGroup `
        --image $acrImage `
        --cpu $Cpu `
        --memory $Memory `
        --min-replicas $MinReplicas `
        --max-replicas $MaxReplicas | Out-Null
    if (-not (Az-Succeeded)) { throw "Failed to update container app: $AppName" }
}

Write-Host "Ensuring system identity + ACR pull ..."
az containerapp identity assign -n $AppName -g $ResourceGroup --system-assigned | Out-Null
if (-not (Az-Succeeded)) { throw "Failed to assign identity to container app: $AppName" }

$principalId = az containerapp show -n $AppName -g $ResourceGroup --query identity.principalId -o tsv
if (-not (Az-Succeeded) -or -not $principalId) { throw "Failed to read container app identity principalId." }
$acrId = az acr show -n $AcrName -g $ResourceGroup --query id -o tsv
if (-not (Az-Succeeded) -or -not $acrId) { throw "Failed to read ACR resource id." }

az role assignment create --assignee $principalId --role AcrPull --scope $acrId 2>$null | Out-Null
az containerapp registry set -n $AppName -g $ResourceGroup --server $acrServer --identity system | Out-Null
if (-not (Az-Succeeded)) { throw "Failed to set container app registry managed identity." }

$fqdn = az containerapp show `
    --name $AppName `
    --resource-group $ResourceGroup `
    --query "properties.configuration.ingress.fqdn" -o tsv

Write-Host ""
Write-Host "Deployment complete."
Write-Host "URL: https://$fqdn"
Write-Host "Health: https://$fqdn/api/health"
Write-Host "OCR:    POST https://$fqdn/api/extract_text  (multipart: file, engine=tesseract|paddle)"
Write-Host "EML:    POST https://$fqdn/api/emltopdfconvert (multipart: file)"

Write-Host ""
Write-Host "Note: health probes were not configured via CLI flags because your installed 'containerapp' extension does not support them."
