$ErrorActionPreference = 'Stop'
$taskConfig = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'studio_config.json') -Raw | ConvertFrom-Json
$taskUrl = 'http://127.0.0.1:' + $taskConfig.port
$taskState = Invoke-RestMethod ($taskUrl + '/api/state') -TimeoutSec 5
if ($taskState.current -or @($taskState.jobs | Where-Object status -eq 'running').Count) {
    throw 'Há um trabalho em execução. Espere terminar para atualizar o estúdio.'
}
$taskListener = Get-NetTCPConnection -LocalPort $taskConfig.port -State Listen | Select-Object -First 1
$taskServer = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $taskListener.OwningProcess)
if ($taskServer.CommandLine -notmatch 'Resources[\\/]Studio[\\/]server.py') {
    throw 'O processo da porta não foi reconhecido como ez3D. Nada foi encerrado.'
}
Stop-Process -Id $taskServer.ProcessId
& (Join-Path $PSScriptRoot 'Start-ez3D.ps1')
