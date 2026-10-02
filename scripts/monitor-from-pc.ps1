# KR-bot Monitor from the Windows PC, through an SSH tunnel.
# krbot's monitor server only listens on the robot's localhost (127.0.0.1:5765), and ufw only
# allows SSH, so the PC reaches it by forwarding a local port over SSH — no firewall change,
# and the monitor link is encrypted.
#
# Usage (VS Code terminal, from Software\code):
#   .\scripts\monitor-from-pc.ps1
#   .\scripts\monitor-from-pc.ps1 -HostAddr 192.168.1.116 -LocalPort 15765

param(
    [string]$HostAddr = "192.168.1.116",
    [string]$User = "beagle",
    [int]$LocalPort = 15765,
    [int]$RemotePort = 5765
)

$ErrorActionPreference = "Continue"
$RepoDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "==> SSH tunnel localhost:$LocalPort -> ${User}@${HostAddr}:127.0.0.1:$RemotePort" -ForegroundColor Cyan
$tunnel = Start-Process -FilePath "ssh" -PassThru -WindowStyle Hidden -ArgumentList @(
    "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=5", "-o", "LogLevel=ERROR",
    "-L", "${LocalPort}:127.0.0.1:${RemotePort}", "${User}@${HostAddr}")
Start-Sleep -Seconds 2
if ($tunnel.HasExited) {
    Write-Host "==> Tunnel failed (exit $($tunnel.ExitCode)). Check: ssh ${User}@${HostAddr}" -ForegroundColor Red
    exit 1
}
try {
    Write-Host "==> Starting KR-bot Monitor (close the window to end the tunnel)" -ForegroundColor Cyan
    py -3 (Join-Path $RepoDir "krbot_monitor_gui.py") --host 127.0.0.1 --port $LocalPort
} finally {
    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force }
    Write-Host "==> Tunnel closed." -ForegroundColor DarkGray
}
