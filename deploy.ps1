# Deploy KRC-Robot bring-up tools to the BeagleY-AI
# Run from the VS Code terminal (PowerShell). Adapted from the atr-viu-emulator deploy flow.
#
# Usage:
#   .\deploy.ps1                                   # defaults: beagle@192.168.1.116
#   .\deploy.ps1 -HostAddr 192.168.1.116 -User beagle
#   .\deploy.ps1 -SetupKeys                        # one-time: install SSH key (then no prompts)
#
# Uses tar (built into Windows 10+) rather than Compress-Archive: PS 5.1 writes
# backslash paths into zips, which unzip on Linux mangles.

param(
    [string]$HostAddr = "192.168.1.116",
    [string]$User = "beagle",
    [switch]$SetupKeys
)

# "Continue", not "Stop": PS 5.1 turns any native stderr (e.g. the board's SSH login
# banner) into a terminating error. Native failures are checked via $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"
$SshOpts = @("-o", "LogLevel=ERROR")
$Target = "${User}@${HostAddr}"
$REMOTE_DIR = "/home/$User/krc-robot"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($SetupKeys) {
    $keyPath = "$env:USERPROFILE\.ssh\id_ed25519"
    if (-not (Test-Path "$keyPath.pub")) {
        Write-Host "==> Generating SSH key..." -ForegroundColor Cyan
        ssh-keygen -t ed25519 -f $keyPath -N '""'
    }
    Write-Host "==> Copying public key to ${Target} (enter password one last time)..." -ForegroundColor Cyan
    Get-Content "$keyPath.pub" | ssh $Target "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
}

Write-Host "==> Deploying KRC-Robot bring-up to ${Target}:${REMOTE_DIR}" -ForegroundColor Cyan

$bundle = Join-Path $env:TEMP "krc-robot-deploy.tgz"
if (Test-Path $bundle) { Remove-Item $bundle -Force }
tar -czf $bundle -C $ScriptDir --exclude "__pycache__" --exclude "*.pyc" --exclude "krbot/build" `
    krc krbot tools tests scripts udev systemd docs notes images web krc_robot_gui.py krbot_monitor_gui.py krbot_web.py remote-setup.sh README.md
if ($LASTEXITCODE -ne 0) { throw "tar failed" }
$sizeKB = [math]::Round((Get-Item $bundle).Length / 1KB, 1)
Write-Host "    Bundle: ${sizeKB} KB" -ForegroundColor DarkGray

Write-Host "==> Transferring..." -ForegroundColor Yellow
scp @SshOpts $bundle "${Target}:/tmp/krc-robot-deploy.tgz"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }
Remove-Item $bundle -Force

Write-Host "==> Installing on BeagleY-AI..." -ForegroundColor Yellow
ssh @SshOpts $Target "mkdir -p $REMOTE_DIR && tar -xzf /tmp/krc-robot-deploy.tgz -C $REMOTE_DIR && rm -f /tmp/krc-robot-deploy.tgz && bash $REMOTE_DIR/remote-setup.sh"
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> WARNING: remote setup exited non-zero (see output above)" -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "==> Deploy complete." -ForegroundColor Green
