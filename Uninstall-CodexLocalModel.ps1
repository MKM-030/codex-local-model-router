[CmdletBinding()]
param(
    [string]$CodexHome = (Join-Path $HOME ".codex"),
    [switch]$RestartChatGPT,
    [switch]$KeepRouterFiles
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$InstallDir = Join-Path $CodexHome "local-model-router"
$ConfigPath = Join-Path $CodexHome "config.toml"
$StatePath = Join-Path $InstallDir "install-state.json"

if (-not (Test-Path $StatePath)) {
    throw "Install state not found at $StatePath. Refusing to guess previous config values."
}
$state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
$configText = [IO.File]::ReadAllText($ConfigPath)
$backup = "$ConfigPath.backup-before-local-model-uninstall-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item -LiteralPath $ConfigPath -Destination $backup -Force
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

function Remove-TopLevelKey([string]$Text, [string]$Key) {
    $lines = [System.Collections.Generic.List[string]]::new()
    ($Text -split "\r?\n", -1) | ForEach-Object { [void]$lines.Add($_) }
    $sectionAt = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^\s*\[') { $sectionAt = $i; break }
    }
    for ($i = $sectionAt - 1; $i -ge 0; $i--) {
        if ($lines[$i] -match ("^\s*" + [regex]::Escape($Key) + "\s*=")) {
            $lines.RemoveAt($i)
        }
    }
    return ($lines -join [Environment]::NewLine)
}

function Remove-Section([string]$Text, [string]$Section) {
    $pattern = "(?ms)^\[" + [regex]::Escape($Section) + "\]\r?\n.*?(?=^\[|\z)"
    return [regex]::Replace($Text, $pattern, "", 1)
}

function Set-SectionBlock([string]$Text, [string]$Section, [string]$Block) {
    $textWithout = Remove-Section $Text $Section
    return (
        $textWithout.TrimEnd() + [Environment]::NewLine +
        [Environment]::NewLine + $Block.TrimEnd() + [Environment]::NewLine
    )
}

function Restore-TopLevel([string]$Text, [string]$Key, $PreviousRaw) {
    if ($null -eq $PreviousRaw) { return Remove-TopLevelKey $Text $Key }
    return Set-TopLevelRawValue $Text $Key ([string]$PreviousRaw)
}
$configText = Restore-TopLevel $configText "model" $state.previousModel
$configText = Restore-TopLevel $configText "model_reasoning_effort" $state.previousReasoning
$configText = Restore-TopLevel $configText "model_provider" $state.previousModelProvider

if ($null -eq $state.previousHybridProviderBlock) {
    $configText = Remove-Section $configText "model_providers.hybrid_router"
} else {
    $configText = Set-SectionBlock $configText "model_providers.hybrid_router" ([string]$state.previousHybridProviderBlock)
}

$tmp = "$ConfigPath.local-model-uninstall.$PID.tmp"
[IO.File]::WriteAllText($tmp, $configText, [Text.UTF8Encoding]::new($false))
try {
    Move-Item -LiteralPath $tmp -Destination $ConfigPath -Force
} catch {
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    throw "Could not restore $ConfigPath. It may be locked. Original error: $($_.Exception.Message)"
}

$routerScript = Join-Path $InstallDir "hybrid-model-router.py"
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -eq "pythonw.exe" -and
        $_.CommandLine -like "*$routerScript*"
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
$startupVbs = Join-Path ([Environment]::GetFolderPath("Startup")) "Codex-Local-Model-Router.vbs"
Remove-Item -LiteralPath $startupVbs -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $CodexHome "models_cache.json") -Force -ErrorAction SilentlyContinue

if (-not $KeepRouterFiles) {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
}

if ($RestartChatGPT) {
    Get-Process ChatGPT -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 1
    $app = Get-StartApps |
        Where-Object { $_.Name -eq "ChatGPT" } |
        Select-Object -First 1
    if ($app) {
        Start-Process explorer.exe ("shell:AppsFolder\" + $app.AppID)
    }
}

Write-Host "Codex local-model routing removed." -ForegroundColor Green
Write-Host "Pre-uninstall backup: $backup"
if (-not $RestartChatGPT) {
    Write-Host "Restart ChatGPT Desktop to refresh the model list." -ForegroundColor Yellow
}
