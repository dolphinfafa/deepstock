$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = 'D:\workspace\conda-envs\deepstock\python.exe'
if (-not (Test-Path $python)) { throw 'Dedicated Deepstock interpreter unavailable' }
Set-Location $project
& $python -m scripts.install_observation_tasks --task Deepstock_ADX_Shadow
if ($LASTEXITCODE -ne 0) { throw 'Verified S4U task installation failed; inspect local diagnostics' }
