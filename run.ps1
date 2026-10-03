# QABuddy on Windows. Requires Python 3.11+ and Node.
#   .\run.ps1            install, ingest if empty, build UI, serve
#   .\run.ps1 ingest --full
#   .\run.ps1 eval
#   .\run.ps1 test
#   .\run.ps1 ask "Why did build #142 fail?" --mode rca
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  Write-Host "Created .env from .env.example. Add OPENROUTER_API_KEY + PINECONE_API_KEY, then re-run."
  exit 1
}

$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  python -m venv .venv
  & $py -m pip install -q -r requirements.txt -r requirements-ingest.txt pytest
}

$cmd = if ($args.Count -ge 1) { $args[0] } else { 'up' }
$rest = @($args | Select-Object -Skip 1)
switch ($cmd) {
  'test'   { & $py -m pytest -q tests }
  'ingest' { & $py -m qabuddy ingest @rest }
  'eval'   { & $py -m qabuddy eval }
  'ask'    { & $py -m qabuddy ask @rest }
  default  {
    if (-not (Test-Path 'ui\dist\index.html')) { Push-Location ui; npm install; npm run build; Pop-Location }
    $points = (& $py -c "from qabuddy import store; print(store.count())" 2>$null)
    if (-not $points) { $points = 0 }
    if ([int]$points -eq 0) { Write-Host "Index is empty: ingesting..."; & $py -m qabuddy ingest }
    Write-Host "QABuddy -> http://localhost:8300"
    & $py -m qabuddy serve
  }
}
