# Standalone tests for installer helpers. No installation, model or external service.
[CmdletBinding()]
param([string]$Installer = '')
if ([string]::IsNullOrWhiteSpace($Installer)) {
    $Installer = Join-Path (Split-Path -Parent $PSCommandPath) '..\Install-CodexLocalModel.ps1'
}
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$helpers = @('Assert-LocalEndpointSettings', 'Test-OptionalLocalEndpoint')
foreach ($name in $helpers) {
    $definition = $ast.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    if (-not $definition) { throw "Missing installer helper: $name" }
    . ([scriptblock]::Create($definition.Extent.Text))
}
$script:Calls = 0
$script:MockMode = 'ready'
$script:Passed = 0
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:Passed++
}
# Replace HTTP only inside this test process. The installer itself is never invoked.
function Invoke-RestMethod {
    [CmdletBinding()]
    param([string]$Uri, [int]$TimeoutSec)
    $script:Calls++
    Assert-True ($Uri -eq 'http://127.0.0.1:18826/v1/models') 'Unexpected probe destination'
    Assert-True ($TimeoutSec -eq 5) 'Probe must have a short timeout'
    switch ($script:MockMode) {
        'refused' { throw 'Connection refused' }
        'loading' { throw '503 model loading' }
        'auth' { throw '401 authentication required' }
        'malformed' { return [pscustomobject]@{ status = 'not a model list' } }
        'null' { return $null }
        'empty' { return [pscustomobject]@{ data = @() } }
        'missing_id' { return [pscustomobject]@{ data = @([pscustomobject]@{ name = 'other' }) } }
        'wrong' { return [pscustomobject]@{ data = @([pscustomobject]@{ id = 'other-model' }) } }
        default { return [pscustomobject]@{ data = @([pscustomobject]@{ id = 'test-model' }) } }
    }
}
foreach ($url in @('http://127.0.0.1:18826/v1', 'http://localhost:18826/v1', 'http://[::1]:18826/v1', 'https://127.0.0.1:18826/v1')) {
    Assert-LocalEndpointSettings $url 18831
}
Assert-True ($script:Calls -eq 0) 'Configuration validation must not contact a model server'
foreach ($url in @('', 'not a URL', 'file:///C:/model', 'http://example.com/v1', 'http://user:pass@127.0.0.1:18826/v1', 'http://127.0.0.1:18826/v1?token=x', 'http://127.0.0.1:18826/v1#fragment', 'http://127.0.0.1:18831/v1')) {
    $rejected = $false
    try { Assert-LocalEndpointSettings $url 18831 } catch { $rejected = $true }
    Assert-True $rejected ('Invalid endpoint accepted: ' + $url)
}
foreach ($port in @(0, -1, 65536)) {
    $rejected = $false
    try { Assert-LocalEndpointSettings 'http://127.0.0.1:18826/v1' $port } catch { $rejected = $true }
    Assert-True $rejected 'Invalid router port accepted'
}
foreach ($mode in @('refused', 'loading', 'auth', 'malformed', 'null', 'empty', 'missing_id', 'wrong')) {
    $script:MockMode = $mode
    $result = Test-OptionalLocalEndpoint 'http://127.0.0.1:18826/v1' 'test-model' 3>$null
    Assert-True ($result -is [bool] -and -not $result) ('Optional check must return false without throwing: ' + $mode)
}
$script:MockMode = 'ready'
$result = Test-OptionalLocalEndpoint 'http://127.0.0.1:18826/v1/' 'test-model'
Assert-True ($result -is [bool] -and $result) 'Ready endpoint was not recognized'
Assert-True ($script:Calls -eq 9) 'Unexpected probe count'
Write-Output "PASS: $script:Passed assertions; no model, installation, or external HTTP request used."
