[CmdletBinding()]
param(
    [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }),
    [string]$ModelId = 'Qwen3.8-Flash-Next',
    [string]$DisplayName = '',
    [string]$LocalBaseUrl = 'http://127.0.0.1:8826/v1',
    [int]$RouterPort = 8831,
    [switch]$AllowCloudSearch,
    [string]$SearchModel = '',
    [switch]$NoStart,
    [string]$SourceRef = 'v0.2.1'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($AllowCloudSearch -and [string]::IsNullOrWhiteSpace($SearchModel)) {
    throw 'Supply -SearchModel with an authorized cloud model. Standalone search sends queries and recent Codex context to OpenAI.'
}
$ConfigPath = Join-Path $CodexHome 'config.toml'
$NormalDir = Join-Path $CodexHome 'local-model-router'
$Legacy = $false
if (Test-Path -LiteralPath (Join-Path $NormalDir 'router-config.json')) {
    $InstallDir = $NormalDir
} elseif (Test-Path -LiteralPath (Join-Path $CodexHome 'hybrid-model-router.py')) {
    $InstallDir = $CodexHome
    $Legacy = $true
} else {
    throw 'No existing router installation found. Run Install-CodexLocalModel.ps1 first.'
}
$RouterScript = Join-Path $InstallDir 'hybrid-model-router.py'
$RouterConfigPath = Join-Path $InstallDir 'router-config.json'
$PythonExe = (& python -c 'import sys; print(sys.executable)')
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
$PythonExe = $PythonExe.Trim()
& $PythonExe -c 'import sys; assert sys.version_info >= (3,11)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
$PythonwExe = Join-Path (Split-Path $PythonExe) 'pythonw.exe'
& $PythonExe -m pip install --user 'httpx>=0.27,<1' 'zstandard>=0.23,<1'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$Backup = Join-Path (Join-Path $CodexHome 'backups') ('tool-bridge-update-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $Backup -Force | Out-Null
foreach ($p in @($ConfigPath,$RouterScript,$RouterConfigPath,(Join-Path $InstallDir 'tool_bridge.py'))) {
    if (Test-Path -LiteralPath $p) { Copy-Item -LiteralPath $p -Destination $Backup -Force }
}
function Copy-SourceFile([string]$Relative, [string]$Target) {
    $source = Join-Path $PSScriptRoot ($Relative -replace '/', '\')
    $parent = Split-Path $Target
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $Target -Force }
    else { Invoke-WebRequest -UseBasicParsing -Uri ('https://raw.githubusercontent.com/MKM-030/codex-local-model-router/' + $SourceRef + '/' + $Relative) -OutFile $Target }
}
Copy-SourceFile 'router/hybrid-model-router.py' $RouterScript
Copy-SourceFile 'router/tool_bridge.py' (Join-Path $InstallDir 'tool_bridge.py')
Copy-SourceFile 'scripts/configure_tool_bridge.py' (Join-Path $InstallDir 'scripts\configure_tool_bridge.py')
if (Test-Path -LiteralPath $RouterConfigPath) {
    $settings = Get-Content -LiteralPath $RouterConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
} else {
    $legacyCatalog = Join-Path $CodexHome 'qwen-flash-next-models.json'
    if (-not (Test-Path -LiteralPath $legacyCatalog)) { throw 'Legacy model catalog missing.' }
    $settings = [pscustomobject]@{
        host = '127.0.0.1'; port = $RouterPort; cloudBase = 'https://chatgpt.com/backend-api/codex'
        models = @([pscustomobject]@{id=$ModelId; baseUrl=$LocalBaseUrl; catalogPath=$legacyCatalog})
    }
}
if (-not [string]::IsNullOrWhiteSpace($DisplayName) -and -not @($settings.models | Where-Object { $_.id -eq $ModelId }).Count) {
    throw 'No configured model matches -ModelId; refusing display-name repair.'
}
$settings | Add-Member -NotePropertyName logMetadata -NotePropertyValue $true -Force
if ($AllowCloudSearch) {
    $settings | Add-Member -NotePropertyName allowCloudSearch -NotePropertyValue $true -Force
    $settings | Add-Member -NotePropertyName searchModel -NotePropertyValue $SearchModel -Force
}
foreach ($m in $settings.models) {
    $catalogPath = [string]$m.catalogPath
    if (-not [IO.Path]::IsPathRooted($catalogPath)) { $catalogPath = Join-Path $InstallDir $catalogPath }
    Copy-Item -LiteralPath $catalogPath -Destination (Join-Path $Backup ([IO.Path]::GetFileName($catalogPath))) -Force
    $catalog = Get-Content -LiteralPath $catalogPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($entry in $catalog.models) {
        # Explicit repair only; preserve every other model and user-defined label.
        if (-not [string]::IsNullOrWhiteSpace($DisplayName) -and $entry.slug -eq $ModelId) {
            $entry | Add-Member -NotePropertyName display_name -NotePropertyValue $DisplayName -Force
        }
        foreach ($key in @('include_skills_usage_instructions','include_plugin_usage_instructions','include_apps_usage_instructions')) {
            $entry | Add-Member -NotePropertyName $key -NotePropertyValue $true -Force
        }
        $entry | Add-Member -NotePropertyName use_responses_lite -NotePropertyValue $false -Force
    }
    [IO.File]::WriteAllText($catalogPath,($catalog | ConvertTo-Json -Depth 60),[Text.UTF8Encoding]::new($false))
}
[IO.File]::WriteAllText($RouterConfigPath,($settings | ConvertTo-Json -Depth 30),[Text.UTF8Encoding]::new($false))
& $PythonExe -m py_compile $RouterScript (Join-Path $InstallDir 'tool_bridge.py')
if ($LASTEXITCODE -ne 0) { throw 'Router compilation failed; backup preserved.' }
& $PythonExe (Join-Path $InstallDir 'scripts\configure_tool_bridge.py') --config $ConfigPath --state (Join-Path $InstallDir 'tool-bridge-state.json')
if ($LASTEXITCODE -ne 0) { throw 'Codex config update failed; restore backup if necessary.' }
$cache = Join-Path $CodexHome 'models_cache.json'
if (Test-Path -LiteralPath $cache) {
    Copy-Item -LiteralPath $cache -Destination (Join-Path $Backup 'models_cache.json')
    Remove-Item -LiteralPath $cache -Force
}
if (-not $NoStart) {
    # Stop only Python processes executing this installation's exact script path.
    $owned = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -and
        $_.CommandLine.IndexOf($RouterScript,[StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    foreach ($process in $owned) { Stop-Process -Id $process.ProcessId -ErrorAction Stop }
    $startupName = if ($Legacy) { 'ChatGPT-Hybrid-Model-Router.vbs' } else { 'Codex-Local-Model-Router.vbs' }
    $startupPath = Join-Path ([Environment]::GetFolderPath('Startup')) $startupName
    $vbs = 'Set shell = CreateObject("WScript.Shell")' + [Environment]::NewLine + 'shell.Run """' + $PythonwExe + '"" ""' + $RouterScript + '""", 0, False' + [Environment]::NewLine
    if (Test-Path -LiteralPath $startupPath) { Copy-Item -LiteralPath $startupPath -Destination $Backup -Force }
    [IO.File]::WriteAllText($startupPath,$vbs,[Text.UTF8Encoding]::new($false))
    Start-Process -FilePath $PythonwExe -ArgumentList ('"' + $RouterScript + '"')
    $health = $null
    for ($attempt=0; $attempt -lt 20; $attempt++) {
        try { $health=Invoke-RestMethod -Uri ('http://127.0.0.1:' + $settings.port + '/health') -TimeoutSec 1; break }
        catch { Start-Sleep -Milliseconds 250 }
    }
    if (-not $health -or $health.version -ne '0.2.1') { throw 'The new router failed its health check; backup preserved.' }
    Write-Host ('Router running: v' + $health.version + ' PID ' + $health.pid)
}
Write-Host ('Backup: ' + $Backup)
Write-Host 'Tool bridge installed. Restart Codex Desktop when active work is finished to reload its feature/model metadata.'
if ($AllowCloudSearch) { Write-Host 'Standalone web search is external: Codex sends search requests and recent context to OpenAI.' }
