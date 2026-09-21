[CmdletBinding()]
param(
    [string]$CodexHome = (Join-Path $HOME ".codex"),
    [switch]$RunInferenceProbe
)

$ErrorActionPreference = "Continue"
$ConfigPath = Join-Path $CodexHome "config.toml"
$InstallDir = Join-Path $CodexHome "local-model-router"
$RouterConfigPath = Join-Path $InstallDir "router-config.json"
if (-not (Test-Path $RouterConfigPath) -and (Test-Path (Join-Path $CodexHome "router-config.json"))) {
    $InstallDir = $CodexHome
    $RouterConfigPath = Join-Path $CodexHome "router-config.json"
}
$CachePath = Join-Path $CodexHome "models_cache.json"

Write-Host "Codex local-model router diagnostics" -ForegroundColor Cyan
Write-Host "Codex home: $CodexHome"

if (-not (Test-Path $ConfigPath)) {
    Write-Host "[FAIL] config.toml not found" -ForegroundColor Red
    exit 1
}

try {
    $stream = [IO.File]::Open(
        $ConfigPath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None
    )
    $stream.Close()
    Write-Host "[ OK ] config.toml is writable and not exclusively locked"
} catch {
    Write-Host "[FAIL] config.toml is locked or not writable" -ForegroundColor Red
    $lockScript = Join-Path $PSScriptRoot "Get-FileLockOwner.ps1"
    if (Test-Path $lockScript) {
        $owners = & $lockScript -Path $ConfigPath
        foreach ($owner in $owners) {
            Write-Host "       lock owner: $owner" -ForegroundColor Yellow
        }
    }
}

if (-not (Test-Path $RouterConfigPath)) {
    Write-Host "[FAIL] router-config.json not found" -ForegroundColor Red
    exit 1
}
$routerConfig = Get-Content -LiteralPath $RouterConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$routerHost = [string]$routerConfig.host
$routerPort = [int]$routerConfig.port
$model = $routerConfig.models | Select-Object -First 1
$modelId = [string]$model.id
$localBaseUrl = ([string]$model.baseUrl).TrimEnd("/")

try {
    $tcp = [Net.Sockets.TcpClient]::new()
    $tcp.Connect($routerHost, $routerPort)
    $tcp.Close()
    Write-Host "[ OK ] router is listening on $($routerHost):$routerPort"
} catch {
    Write-Host "[FAIL] router is not listening on $($routerHost):$routerPort" -ForegroundColor Red
}

try {
    $models = Invoke-RestMethod -Uri "$localBaseUrl/models" -TimeoutSec 5
    Write-Host "[ OK ] local model API is reachable: $localBaseUrl"
} catch {
    Write-Host "[FAIL] local model API is not reachable: $localBaseUrl" -ForegroundColor Red
}

if (Test-Path $CachePath) {
    try {
        $cache = Get-Content -LiteralPath $CachePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $found = $cache.models | Where-Object { $_.slug -eq $modelId }
        if ($found) {
            Write-Host "[ OK ] model is present in Codex model cache: $modelId"
        } else {
            Write-Host "[WARN] model not present in models_cache.json; restart ChatGPT Desktop" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "[WARN] could not parse models_cache.json" -ForegroundColor Yellow
    }
} else {
    Write-Host "[WARN] models_cache.json not found yet; restart ChatGPT Desktop" -ForegroundColor Yellow
}

if ($RunInferenceProbe) {
    $probe = @{
        model = $modelId
        input = "Reply exactly LOCAL_MODEL_OK"
        max_output_tokens = 64
        stream = $false
    } | ConvertTo-Json -Compress
    try {
        $response = Invoke-RestMethod -Uri "http://$($routerHost):$routerPort/v1/responses" -Method Post -ContentType "application/json" -Body $probe -TimeoutSec 90
        $message = $response.output | Where-Object { $_.type -eq "message" } | Select-Object -First 1
        Write-Host "[ OK ] inference probe: $($message.content[0].text)"
    } catch {
        Write-Host "[FAIL] inference probe failed: $($_.Exception.Message)" -ForegroundColor Red
    }
}
