<#
.SYNOPSIS
  One-command local stack for ParallaxOps (Windows, no Docker).

.EXAMPLE
  .\scripts\dev.ps1 setup      # tools, Python venv, pnpm install, Postgres init (idempotent)
  .\scripts\dev.ps1 up         # Postgres, S3 (SeaweedFS), Temporal, API, worker, web
  .\scripts\dev.ps1 status
  .\scripts\dev.ps1 logs api
  .\scripts\dev.ps1 down
#>
param(
  [Parameter(Position = 0)][ValidateSet('setup', 'up', 'down', 'status', 'logs', 'restart')][string]$Cmd = 'status',
  [Parameter(Position = 1)][string]$Svc = ''
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Home_ = if ($env:PARALLAXOPS_HOME) { $env:PARALLAXOPS_HOME } else { Join-Path $env:LOCALAPPDATA 'ParallaxOps' }
$env:PARALLAXOPS_HOME = $Home_
$Tools = Join-Path $Home_ 'tools'
$Run = Join-Path $Home_ 'run'; $Logs = Join-Path $Home_ 'logs'
$Py = Join-Path $Home_ 'venv\Scripts\python.exe'
New-Item -ItemType Directory -Force $Run, $Logs | Out-Null
$NodeDir = (Get-ChildItem (Join-Path $Tools 'node') -Directory -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
$env:PATH = (@($NodeDir, (Join-Path $Tools 'pnpm')) | Where-Object { $_ }) -join ';' + ";$env:PATH"

# name -> port used for health/wait checks
$Ports = [ordered]@{ postgres = 5433; s3 = 8333; temporal = 7233; api = 4000; worker = 9108; web = 3000 }

function Test-Port($port) { [bool](Test-NetConnection 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue) }
function Wait-Port($name, $port, $seconds = 60) {
  $t = 0
  while (-not (Test-Port $port) -and $t -lt $seconds) { Start-Sleep 1; $t++ }
  if (-not (Test-Port $port)) { throw "$name did not open port $port within ${seconds}s; see $Logs\$name.*.log" }
}
function Start-Svc($name, $exe, $arguments, $cwd = $Repo, $envs = @{}) {
  $pidFile = Join-Path $Run "$name.pid"
  if ((Test-Path $pidFile) -and (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)) { Write-Host "$name already running"; return }
  foreach ($k in $envs.Keys) { [Environment]::SetEnvironmentVariable($k, $envs[$k], 'Process') }
  $p = Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $cwd -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $Logs "$name.out.log") -RedirectStandardError (Join-Path $Logs "$name.err.log")
  Set-Content $pidFile $p.Id
}
function Stop-Svc($name) {
  $pidFile = Join-Path $Run "$name.pid"
  if (Test-Path $pidFile) {
    $id = [int](Get-Content $pidFile)
    if (Get-Process -Id $id -ErrorAction SilentlyContinue) { & taskkill /PID $id /T /F | Out-Null }
    Remove-Item $pidFile
  }
}

function Setup {
  & $Py -c "import sys" 2>$null
  if ($LASTEXITCODE -ne 0) {
    uv venv --python 3.14 (Join-Path $Home_ 'venv')
  }
  uv pip install --python $Py -e "$Repo\services\vision-worker[dev,worker]" check-jsonschema types-psutil
  & $Py "$Repo\scripts\setup_tools.py"
  $NodeDir = (Get-ChildItem (Join-Path $Tools 'node') -Directory | Select-Object -First 1).FullName
  $env:PATH = "$NodeDir;$(Join-Path $Tools 'pnpm');$env:PATH"
  Push-Location $Repo; try { pnpm install } finally { Pop-Location }
  $data = Join-Path $Home_ 'pg\data'
  if (-not (Test-Path (Join-Path $data 'PG_VERSION'))) {
    New-Item -ItemType Directory -Force (Join-Path $Home_ 'pg') | Out-Null
    $pw = Join-Path $Home_ 'pg\pwfile'; Set-Content $pw 'parallaxops' -NoNewline
    & (Join-Path $Tools 'postgres\bin\initdb.exe') -D $data -U parallaxops --auth=scram-sha-256 "--pwfile=$pw" -E UTF8 --locale=C
  }
  $s3 = Join-Path $Home_ 's3'; New-Item -ItemType Directory -Force (Join-Path $s3 'data') | Out-Null
  if (-not (Test-Path (Join-Path $s3 's3.json'))) {
    # Local development credentials only. Rotate and move to a secret store for anything shared.
    Set-Content (Join-Path $s3 's3.json') '{"identities":[{"name":"parallaxops","credentials":[{"accessKey":"dev-access-key","secretKey":"dev-secret-key-change-me-locally"}],"actions":["Admin","Read","Write","List","Tagging"]}]}' -NoNewline
  }
  Write-Host 'setup complete'
}

function Up($only) {
  $want = { param($n) (-not $only) -or $only -eq $n }
  if (& $want 'postgres') {
    $pgb = Join-Path $Tools 'postgres\bin'; $data = Join-Path $Home_ 'pg\data'
    if (-not (Test-Port 5433)) {
      # pg_ctl -w hangs on Windows (inherited handles), so launch via Start-Process and poll the port.
      Start-Process -FilePath "$pgb\pg_ctl.exe" -ArgumentList @('start', '-D', $data, '-o', '"-p 5433 -c listen_addresses=127.0.0.1 -c shared_buffers=128MB -c max_connections=50"', '-l', (Join-Path $Logs 'postgres.log')) -WindowStyle Hidden | Out-Null
    }
    Wait-Port postgres 5433
    & $Py -I -c "import socket,sys;s=socket.create_connection(('127.0.0.1',5433),3)" 2>$null
    # create the database on first boot (no psql shipped; use node `pg` through the API's migrate step)
  }
  if (& $want 's3') {
    Start-Svc s3 (Join-Path $Tools 'seaweedfs\weed.exe') @('server', "-dir=$(Join-Path $Home_ 's3\data')", '-ip=127.0.0.1', '-ip.bind=127.0.0.1', '-s3', '-s3.port=8333', "-s3.config=$(Join-Path $Home_ 's3\s3.json')", '-s3.allowedOrigins=http://localhost:3000,http://127.0.0.1:3000', '-master.volumeSizeLimitMB=256', '-volume.max=30')
    Wait-Port s3 8333
  }
  if (& $want 'temporal') {
    New-Item -ItemType Directory -Force (Join-Path $Home_ 'temporal') | Out-Null
    Start-Svc temporal (Join-Path $Tools 'temporal\temporal.exe') @('server', 'start-dev', '--ip', '127.0.0.1', '--port', '7233', '--ui-port', '8233', '--headless', '--db-filename', (Join-Path $Home_ 'temporal\temporal.db'), '--log-level', 'warn')
    Wait-Port temporal 7233
  }
  if (& $want 'api') {
    $node = Join-Path $NodeDir 'node.exe'
    Start-Svc api $node @('src/server.ts') (Join-Path $Repo 'apps\api')
    Wait-Port api 4000 90
  }
  if (& $want 'worker') {
    Start-Svc worker $Py @('-m', 'parallaxops_vision.worker.main') $Repo @{ PYTHONUTF8 = '1' }
    Wait-Port worker 9108 60
  }
  if ((& $want 'web') -and (Test-Path (Join-Path $Repo 'apps\web\package.json'))) {
    $pnpm = Join-Path $Tools 'pnpm\pnpm.cmd'
    Start-Svc web $pnpm @('--filter', '@parallaxops/web', 'start') $Repo
    Wait-Port web 3000 90
  }
  Status
}

function Down($only) {
  foreach ($n in @('web', 'worker', 'api', 'temporal', 's3')) { if ((-not $only) -or $only -eq $n) { Stop-Svc $n } }
  if ((-not $only) -or $only -eq 'postgres') {
    & (Join-Path $Tools 'postgres\bin\pg_ctl.exe') stop -D (Join-Path $Home_ 'pg\data') -m fast 2>$null | Out-Null
  }
}

function Status {
  foreach ($n in $Ports.Keys) {
    $up = Test-Port $Ports[$n]
    '{0,-9} :{1,-5} {2}' -f $n, $Ports[$n], $(if ($up) { 'up' } else { 'down' })
  }
}

switch ($Cmd) {
  'setup'   { Setup }
  'up'      { Up $Svc }
  'down'    { Down $Svc }
  'restart' { Down $Svc; Up $Svc }
  'status'  { Status }
  'logs'    { if (-not $Svc) { throw 'usage: dev.ps1 logs <service>' }; Get-Content (Join-Path $Logs "$Svc.*.log") -Tail 60 }
}
