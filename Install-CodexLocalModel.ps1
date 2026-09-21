[CmdletBinding()]
param(
    [string]$ModelId = "Qwen3.8-Flash-Next",
    [string]$DisplayName = "Qwen3.8 Flash Next - Local",
    [string]$LocalBaseUrl = "http://127.0.0.1:8826/v1",
    [int]$RouterPort = 8831,
    [int]$ContextWindow = 262144,
    [ValidateSet("low","medium","xhigh")]
    [string]$DefaultReasoning = "low",
    [string[]]$SupportedReasoning = @("low","medium","xhigh"),
    [string]$CodexHome = (Join-Path $HOME ".codex"),
    [switch]$RestartChatGPT,
    [switch]$RunInferenceProbe,
    [switch]$SkipAutostart,
    [switch]$AllowCloudSearch,
    [string]$SearchModel = "",
    [string]$SourceRef = "v0.2.0"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRawBase = "https://raw.githubusercontent.com/MKM-030/codex-local-model-router/$SourceRef"
if ($AllowCloudSearch -and [string]::IsNullOrWhiteSpace($SearchModel)) { throw "Supply -SearchModel: cloud web search sends queries and recent context to OpenAI." }
$InstallDir = Join-Path $CodexHome "local-model-router"
$ConfigPath = Join-Path $CodexHome "config.toml"
$RouterScript = Join-Path $InstallDir "hybrid-model-router.py"
$RouterConfigPath = Join-Path $InstallDir "router-config.json"
$CatalogPath = Join-Path $InstallDir "local-model-catalog.json"
$StatePath = Join-Path $InstallDir "install-state.json"

function Resolve-PythonExe {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $resolved = & $python.Source -c "import sys; print(sys.executable)"
        if ($LASTEXITCODE -eq 0 -and $resolved) { return $resolved.Trim() }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $resolved = & $py.Source -3 -c "import sys; print(sys.executable)"
        if ($LASTEXITCODE -eq 0 -and $resolved) { return $resolved.Trim() }
    }
    throw "Python 3 is required. Install Python, then rerun this installer."
}

function Get-TopLevelRawValue([string]$Text, [string]$Key) {
    $lines = $Text -split "\r?\n", -1
    foreach ($line in $lines) {
        if ($line -match '^\s*\[') { break }
        if ($line -match ("^\s*" + [regex]::Escape($Key) + "\s*=\s*(.+?)\s*$")) {
            return $Matches[1]
        }
    }
    return $null
}

function Set-TopLevelRawValue([string]$Text, [string]$Key, [string]$RawValue) {
    $lines = [System.Collections.Generic.List[string]]::new()
    ($Text -split "\r?\n", -1) | ForEach-Object { [void]$lines.Add($_) }
    $sectionAt = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^\s*\[') { $sectionAt = $i; break }
    }
    for ($i = 0; $i -lt $sectionAt; $i++) {
        if ($lines[$i] -match ("^\s*" + [regex]::Escape($Key) + "\s*=")) {
            $lines[$i] = "$Key = $RawValue"
            return ($lines -join [Environment]::NewLine)
        }
    }
    $lines.Insert($sectionAt, "$Key = $RawValue")
    return ($lines -join [Environment]::NewLine)
}

function Get-SectionBlock([string]$Text, [string]$Section) {
    $pattern = "(?ms)^\[" + [regex]::Escape($Section) + "\]\r?\n.*?(?=^\[|\z)"
    $match = [regex]::Match($Text, $pattern)
    if ($match.Success) { return $match.Value.TrimEnd() }
    return $null
}

function Set-SectionBlock([string]$Text, [string]$Section, [string]$Block) {
    $pattern = "(?ms)^\[" + [regex]::Escape($Section) + "\]\r?\n.*?(?=^\[|\z)"
    if ([regex]::IsMatch($Text, $pattern)) {
        return [regex]::Replace(
            $Text,
            $pattern,
            ($Block.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine),
            1
        )
    }
    return (
        $Text.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine +
        $Block.TrimEnd() + [Environment]::NewLine
    )
}

function Write-Utf8Atomic([string]$Path, [string]$Text) {
    $tmp = "$Path.local-model-router.$PID.tmp"
    $utf8 = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText($tmp, $Text, $utf8)
    try {
        Move-Item -LiteralPath $tmp -Destination $Path -Force
    } catch {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        throw "Could not update $Path. It may be locked by another process. Original error: $($_.Exception.Message)"
    }
}

if (-not (Test-Path $CodexHome)) {
    New-Item -ItemType Directory -Path $CodexHome -Force | Out-Null
}
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

$PythonExe = Resolve-PythonExe
$PythonwExe = Join-Path (Split-Path $PythonExe) "pythonw.exe"
if (-not (Test-Path $PythonwExe)) {
    throw "pythonw.exe was not found next to $PythonExe"
}

& $PythonExe -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11+ is required." }
$missing = & $PythonExe -c "import importlib.util; print(','.join(m for m in ['httpx','zstandard'] if importlib.util.find_spec(m) is None))"
if (-not [string]::IsNullOrWhiteSpace([string]$missing)) {
    & $PythonExe -m pip install --user "httpx>=0.27,<1" "zstandard>=0.23,<1"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install router dependencies." }
}

try {
    $null = Invoke-RestMethod -Uri ($LocalBaseUrl.TrimEnd("/") + "/models") -TimeoutSec 5
    Write-Host "Local model endpoint reachable: $LocalBaseUrl"
} catch {
    throw "Local endpoint is not reachable at $LocalBaseUrl. Start it first. $($_.Exception.Message)"
}

function Install-RepoFile([string]$RelativePath, [string]$Destination) {
    $localRelative = $RelativePath -replace '/', '\'
    $localSource = Join-Path $PSScriptRoot $localRelative
    $parent = Split-Path $Destination
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    if (Test-Path $localSource) {
        Copy-Item -LiteralPath $localSource -Destination $Destination -Force
    } else {
        Invoke-WebRequest -UseBasicParsing -Uri "$RepoRawBase/$RelativePath" -OutFile $Destination
    }
}

Install-RepoFile "router/hybrid-model-router.py" $RouterScript
Install-RepoFile "router/tool_bridge.py" (Join-Path $InstallDir "tool_bridge.py")
Install-RepoFile "scripts/configure_tool_bridge.py" (Join-Path $InstallDir "scripts\configure_tool_bridge.py")
Install-RepoFile "Uninstall-CodexLocalModel.ps1" (Join-Path $InstallDir "Uninstall-CodexLocalModel.ps1")
Install-RepoFile "scripts/Test-CodexLocalModel.ps1" (Join-Path $InstallDir "scripts\Test-CodexLocalModel.ps1")
Install-RepoFile "scripts/Get-FileLockOwner.ps1" (Join-Path $InstallDir "scripts\Get-FileLockOwner.ps1")

$reasoningDescriptions = @{
    low = "Fast local reasoning."
    medium = "Balanced local reasoning."
    xhigh = "Deep local reasoning."
}
$reasoningLevels = @(
    foreach ($effort in $SupportedReasoning) {
        if ($effort -notin @("low","medium","xhigh")) {
            throw "Unsupported reasoning effort '$effort'."
        }
        [ordered]@{
            effort = $effort
            description = $reasoningDescriptions[$effort]
        }
    }
)
if ($DefaultReasoning -notin $SupportedReasoning) {
    throw "DefaultReasoning must also be present in SupportedReasoning."
}

$catalog = [ordered]@{
    models = @(
        [ordered]@{
            slug = $ModelId
            display_name = $DisplayName
            description = "Local model served through an OpenAI Responses-compatible endpoint."
            default_reasoning_level = $DefaultReasoning
            supported_reasoning_levels = $reasoningLevels
            shell_type = "unified_exec"
            visibility = "list"
            supported_in_api = $true
            priority = 100
            additional_speed_tiers = @()
            service_tiers = @()
            availability_nux = $null
            upgrade = $null
            base_instructions = "You are a capable local coding and work assistant. Follow the user's instructions and use available tools when appropriate."
            include_skills_usage_instructions = $true
            include_plugin_usage_instructions = $true
            include_apps_usage_instructions = $true
            supports_reasoning_summary_parameter = $false
            default_reasoning_summary = "none"
            support_verbosity = $false
            default_verbosity = $null
            apply_patch_tool_type = "freeform"
            web_search_tool_type = "text"
            truncation_policy = [ordered]@{ mode = "tokens"; limit = 10000 }

            supports_image_detail_original = $false
            context_window = $ContextWindow
            max_context_window = $ContextWindow
            effective_context_window_percent = 95
            experimental_supported_tools = @()
            input_modalities = @("text")
            supports_search_tool = $false
            supports_experimental_context = $false
            use_responses_lite = $false
            node_repl_auto_review_required = $false
            node_repl_disabled = $false
        }
    )
}
$catalog | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $CatalogPath -Encoding UTF8

$routerConfig = [ordered]@{
    host = "127.0.0.1"
    port = $RouterPort
    cloudBase = "https://chatgpt.com/backend-api/codex"
    allowCloudSearch = [bool]$AllowCloudSearch
    searchModel = $SearchModel
    logMetadata = $true
    models = @(
        [ordered]@{
            id = $ModelId
            baseUrl = $LocalBaseUrl.TrimEnd("/")
            catalogPath = "local-model-catalog.json"
        }
    )
}
$routerConfig | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $RouterConfigPath -Encoding UTF8

if (-not (Test-Path $ConfigPath)) {
    [IO.File]::WriteAllText(
        $ConfigPath,
        "",
        [Text.UTF8Encoding]::new($false)
    )
}
$configText = [IO.File]::ReadAllText($ConfigPath)
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupPath = "$ConfigPath.backup-local-model-router-$timestamp"
Copy-Item -LiteralPath $ConfigPath -Destination $backupPath -Force

$state = [ordered]@{
    installedAt = (Get-Date).ToString("o")
    backupPath = $backupPath
    previousModel = Get-TopLevelRawValue $configText "model"
    previousReasoning = Get-TopLevelRawValue $configText "model_reasoning_effort"
    previousModelProvider = Get-TopLevelRawValue $configText "model_provider"
    previousHybridProviderBlock = Get-SectionBlock $configText "model_providers.hybrid_router"
    modelId = $ModelId
    routerPort = $RouterPort
}
if (-not (Test-Path -LiteralPath $StatePath)) {
    $state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatePath -Encoding UTF8
}

$configText = Set-TopLevelRawValue $configText "model_provider" '"hybrid_router"'

$providerBlock = @"
[model_providers.hybrid_router]
name = "OpenAI + Local Model Router"
base_url = "http://127.0.0.1:$RouterPort/v1"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
supports_standalone_web_search = true
request_max_retries = 2
stream_max_retries = 2
stream_idle_timeout_ms = 600000
"@
$configText = Set-SectionBlock $configText "model_providers.hybrid_router" $providerBlock
Write-Utf8Atomic $ConfigPath $configText
& $PythonExe (Join-Path $InstallDir "scripts\configure_tool_bridge.py") --config $ConfigPath --state (Join-Path $InstallDir "tool-bridge-state.json")
if ($LASTEXITCODE -ne 0) { throw "Could not enable standalone tool mode; restore the config backup before retrying." }

$startupDir = [Environment]::GetFolderPath("Startup")
$startupVbs = Join-Path $startupDir "Codex-Local-Model-Router.vbs"

if (-not $SkipAutostart) {
    $vbs = @"
Set shell = CreateObject("WScript.Shell")
shell.Run """$PythonwExe"" ""$RouterScript""", 0, False
Set shell = Nothing
"@
    [IO.File]::WriteAllText(
        $startupVbs,
        $vbs,
        [Text.UTF8Encoding]::new($false)
    )

    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -in @("pythonw.exe","python.exe") -and
            $_.CommandLine -like "*$RouterScript*"
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }

    Start-Process -FilePath "$env:WINDIR\System32\wscript.exe" -ArgumentList ('"' + $startupVbs + '"')
    Start-Sleep -Milliseconds 800
    $health = Invoke-RestMethod -Uri ("http://127.0.0.1:$RouterPort/health") -TimeoutSec 5
    if ($health.version -ne "0.2.0") { throw "Unexpected router version on target port." }
}

$cache = Join-Path $CodexHome "models_cache.json"
Remove-Item -LiteralPath $cache -Force -ErrorAction SilentlyContinue

if ($RunInferenceProbe) {
    $probe = @{
        model = $ModelId
        input = "Reply exactly LOCAL_MODEL_OK"
        max_output_tokens = 64
        stream = $false
    } | ConvertTo-Json -Compress
    try {
        $null = Invoke-RestMethod -Uri ("http://127.0.0.1:$RouterPort/v1/responses") -Method Post -ContentType "application/json" -Body $probe -TimeoutSec 60
        Write-Host "Local inference probe completed."
    } catch {
        Write-Warning "Router installed, but inference probe failed: $($_.Exception.Message)"
    }
}

if ($RestartChatGPT) {
    Get-Process ChatGPT -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 1
    $app = Get-StartApps |
        Where-Object { $_.Name -eq "ChatGPT" } |
        Select-Object -First 1
    if ($app) {
        Start-Process explorer.exe ("shell:AppsFolder\" + $app.AppID)
    } else {
        Write-Warning "ChatGPT Start-menu entry was not found. Restart ChatGPT manually."
    }
}

Write-Host ""
Write-Host "Installed Codex local-model routing successfully." -ForegroundColor Green
Write-Host "Model:       $ModelId"
Write-Host "Local API:   $LocalBaseUrl"
Write-Host "Router:      http://127.0.0.1:$RouterPort/v1"
Write-Host "Backup:      $backupPath"
Write-Host "Install dir: $InstallDir"
if (-not $RestartChatGPT) {
    Write-Host "Restart ChatGPT Desktop before using the model picker." -ForegroundColor Yellow
}
