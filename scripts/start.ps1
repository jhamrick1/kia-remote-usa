# Starts the car service and the Cloudflare tunnel, and restarts either one if it stops.
# Run by the "kia-remote" scheduled task at logon (scripts\install-startup.ps1) in a console with no
# window. The service and the tunnel share that console (-NoNewWindow): a new console of their own
# would flash up, or open in Windows Terminal when that is the default terminal.
# Logs: %LOCALAPPDATA%\kia-remote\start.log (the service keeps its own server.log there too).
#
# The tunnel runs a saved copy of cloudflared (%LOCALAPPDATA%\kia-remote\cloudflared.exe, through
# CLOUDFLARED_PATH). Without it, wrangler checks for a new cloudflared on every start and, when one
# is out, asks "Download?" and waits for an answer nobody can give in a hidden window: the tunnel
# looks alive but never connects. The saved copy is refreshed at logon once it's 30 days old.
# The tunnel is also restarted when it runs but has no connection to Cloudflare for 90 seconds
# (cloudflared's /ready on TUNNEL_METRICS).

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $env:LOCALAPPDATA 'kia-remote'
New-Item -ItemType Directory -Force $data | Out-Null
$log = Join-Path $data 'start.log'
function Say($m) { Add-Content -Encoding utf8 $log "$(Get-Date -Format s) $m" }

$python = Join-Path $root '.venv\Scripts\python.exe'
$npx = (Get-Command npx.cmd -ErrorAction SilentlyContinue).Source
if (-not $npx) { Say 'npx not found; install Node.js'; exit 1 }

$cloudflared = Join-Path $data 'cloudflared.exe'
$release = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
function Get-Cloudflared {
    $fresh = (Test-Path $cloudflared) -and ((Get-Item $cloudflared).LastWriteTime -gt (Get-Date).AddDays(-30))
    if ($fresh) { return }
    $new = "$cloudflared.new"
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 120 -Uri $release -OutFile $new -ErrorAction Stop
        Move-Item -Force $new $cloudflared -ErrorAction Stop
        (Get-Item $cloudflared).LastWriteTime = Get-Date
        Say "cloudflared downloaded ($(& $cloudflared --version))"
        return
    } catch { Say "cloudflared download failed: $($_.Exception.Message)"; Remove-Item -Force $new -ErrorAction SilentlyContinue }
    if (Test-Path $cloudflared) { return }   # an old copy still works
    # No copy yet and no download: take the one wrangler saved, if any.
    $cached = Get-ChildItem (Join-Path $env:APPDATA 'xdg.config\.wrangler\cloudflared') -Recurse -Filter cloudflared.exe `
        -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($cached) { Copy-Item $cached.FullName $cloudflared; Say "cloudflared copied from wrangler's cache" }
}
Get-Cloudflared
if (Test-Path $cloudflared) { $env:CLOUDFLARED_PATH = $cloudflared }
else { Say 'no cloudflared copy; wrangler will fetch its own (may hang if it asks to download)' }

$metrics = '127.0.0.1:20249'
$env:TUNNEL_METRICS = $metrics   # inherited by wrangler, then by cloudflared

function Test-TunnelReady {
    try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://$metrics/ready" -ErrorAction Stop).StatusCode -eq 200 }
    catch { $false }
}
function Stop-Tunnel($p) { if ($p -and -not $p.HasExited) { taskkill /T /F /PID $p.Id | Out-Null } }

$server = $null; $tunnel = $null; $tunnelSince = $null; $notReady = 0
Say 'start.ps1 starting'
while ($true) {
    if (-not $server -or $server.HasExited) {
        if ($server) { Say "car service stopped (exit $($server.ExitCode)); restarting" }
        $server = Start-Process -FilePath $python -ArgumentList '-m', 'server' `
            -WorkingDirectory $root -NoNewWindow -PassThru
        Say "car service started (pid $($server.Id))"
    }
    if ($tunnel -and -not $tunnel.HasExited -and ((Get-Date) - $tunnelSince).TotalSeconds -ge 90) {
        if (Test-TunnelReady) { $notReady = 0 } else { $notReady++ }
        if ($notReady -ge 3) {
            Say 'tunnel running but not connected for 90 s; restarting'
            Stop-Tunnel $tunnel; $tunnel = $null
        }
    }
    if (-not $tunnel -or $tunnel.HasExited) {
        if ($tunnel) { Say "tunnel stopped (exit $($tunnel.ExitCode)); restarting" }
        $tunnel = Start-Process -FilePath $npx -ArgumentList '--yes', 'wrangler@4', 'tunnel', 'run', 'kia-remote' `
            -WorkingDirectory $env:TEMP -NoNewWindow -PassThru
        $tunnelSince = Get-Date; $notReady = 0
        Say "tunnel started (pid $($tunnel.Id))"
    }
    Start-Sleep -Seconds 30
}
