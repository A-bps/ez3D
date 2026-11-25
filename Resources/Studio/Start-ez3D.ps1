$ErrorActionPreference = 'Stop'
$taskPython = 'B:/models-ai-pipeline/image/venv/Scripts/python.exe'
$taskStudioRoot = $PSScriptRoot
$taskConfig = Get-Content -LiteralPath (Join-Path $taskStudioRoot 'studio_config.json') -Raw | ConvertFrom-Json
$taskUrl = 'http://127.0.0.1:' + $taskConfig.port
try { $null = Invoke-RestMethod ($taskUrl + '/api/state') -TimeoutSec 2 }
catch {
    $taskLogRoot = Join-Path $taskConfig.work_root 'logs'
    New-Item -ItemType Directory -Path $taskLogRoot -Force | Out-Null
    Start-Process -FilePath $taskPython -ArgumentList @('-u', ('"' + (Join-Path $taskStudioRoot 'server.py') + '"')) -WorkingDirectory $taskStudioRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLogRoot 'server.out.log') -RedirectStandardError (Join-Path $taskLogRoot 'server.err.log')
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
        Start-Sleep -Milliseconds 500
        try { $null = Invoke-RestMethod ($taskUrl + '/api/state') -TimeoutSec 1; $taskReady = $true; break } catch {}
    }
    if (!$taskReady) { throw ('O estúdio não iniciou. Consulte ' + $taskLogRoot) }
}
Start-Process $taskUrl
