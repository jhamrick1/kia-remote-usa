# Starts the car service and the Cloudflare tunnel, and restarts either one if it stops.
# Run by the "kia-remote" scheduled task at logon (scripts\install-startup.ps1), hidden.
# Logs: %LOCALAPPDATA%\kia-remote\start.log (the service keeps its own server.log there too).

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $env:LOCALAPPDATA 'kia-remote'
New-Item -ItemType Directory -Force $data | Out-Null
$log = Join-Path $data 'start.log'
function Say($m) { Add-Content -Encoding utf8 $log "$(Get-Date -Format s) $m" }

$python = Join-Path $root '.venv\Scripts\python.exe'
$npx = (Get-Command npx.cmd -ErrorAction SilentlyContinue).Source
if (-not $npx) { Say 'npx not found; install Node.js'; exit 1 }

$server = $null; $tunnel = $null
Say 'start.ps1 starting'
while ($true) {
    if (-not $server -or $server.HasExited) {
        if ($server) { Say "car service stopped (exit $($server.ExitCode)); restarting" }
        $server = Start-Process -FilePath $python -ArgumentList '-m', 'server' `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru
        Say "car service started (pid $($server.Id))"
    }
    if (-not $tunnel -or $tunnel.HasExited) {
        if ($tunnel) { Say "tunnel stopped (exit $($tunnel.ExitCode)); restarting" }
        $tunnel = Start-Process -FilePath $npx -ArgumentList '--yes', 'wrangler@4', 'tunnel', 'run', 'kia-remote' `
            -WorkingDirectory $env:TEMP -WindowStyle Hidden -PassThru
        Say "tunnel started (pid $($tunnel.Id))"
    }
    Start-Sleep -Seconds 30
}
