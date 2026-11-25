$ErrorActionPreference = 'Stop'
$taskModelId = 'ez3d-image-planner'
$taskLms = Join-Path $env:USERPROFILE '.lmstudio/bin/lms.exe'
& $taskLms server start --port 1234 --bind 127.0.0.1
if ($LASTEXITCODE -ne 0) { throw 'Could not start the local LM Studio server.' }
$taskModels = Invoke-RestMethod 'http://127.0.0.1:1234/api/v1/models'
$taskLoaded = @($taskModels.models | ForEach-Object { $_.loaded_instances } | Where-Object { $_.id -eq $taskModelId })
if ($taskLoaded.Count -gt 0) {
    $taskConfig = $taskLoaded[0].config
    if ($taskConfig.speculative_draft_mtp -and $taskConfig.speculative_draft_max_tokens -eq 4) {
        Write-Output 'ez3D image planner is already loaded with MTP 4.'
        exit 0
    }
    & $taskLms unload $taskModelId
    if ($LASTEXITCODE -ne 0) { throw 'Could not unload the previous ez3D image planner.' }
}
& $taskLms load qwen3.5-4b-mtp --identifier $taskModelId --context-length 8192 --gpu max --parallel 1 --speculative-draft-mtp --speculative-draft-max-tokens 4 --yes
if ($LASTEXITCODE -ne 0) { throw 'Could not load Qwen 3.5 4B MTP. No fallback model was substituted.' }
$taskModels = Invoke-RestMethod 'http://127.0.0.1:1234/api/v1/models'
$taskLoaded = @($taskModels.models | ForEach-Object { $_.loaded_instances } | Where-Object { $_.id -eq $taskModelId })
if ($taskLoaded.Count -ne 1 -or !$taskLoaded[0].config.speculative_draft_mtp -or $taskLoaded[0].config.speculative_draft_max_tokens -ne 4) {
    throw 'The local server did not confirm MTP 4.'
}
Write-Output 'Qwen 3.5 4B with vision and MTP 4 is ready. The plugin applies the thinking budget per request.'
