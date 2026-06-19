# WSL + Ubuntu 22.04 Install Script (Target: D:\code\wsl)
# Run as Administrator!
# Usage: powershell -ExecutionPolicy Bypass -File "D:\code\nb_trade\install_wsl.ps1"

$ErrorActionPreference = "Stop"
$WslDir = "D:\code\wsl"
$DistroName = "Ubuntu-22.04"
$TarFile = Join-Path $WslDir "ubuntu-jammy-wsl-amd64.tar.gz"
$DownloadUrl = "https://cloud-images.ubuntu.com/wsl/releases/22.04/current/ubuntu-jammy-wsl-amd64-wsl.rootfs.tar.gz"
$KernelMsi = Join-Path $WslDir "wsl_update_x64.msi"
$KernelUrl = "https://wslstorestorage.blob.core.windows.net/wslblob/wsl_update_x64.msi"

Write-Host "=== WSL + Ubuntu 22.04 Install Script ===" -ForegroundColor Cyan
Write-Host "Target: $WslDir" -ForegroundColor Cyan

if (-not (Test-Path $WslDir)) {
    New-Item -ItemType Directory -Path $WslDir -Force | Out-Null
}

# Step 1: Verify wsl.exe
$wslCmd = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wslCmd) {
    Write-Host "ERROR: wsl.exe not found!" -ForegroundColor Red
    Write-Host "Run these and reboot:" -ForegroundColor Yellow
    Write-Host "  dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart" -ForegroundColor Gray
    Write-Host "  dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart" -ForegroundColor Gray
    exit 1
}
Write-Host "[1/5] wsl.exe OK" -ForegroundColor Green

# Step 2: Install WSL2 kernel update
Write-Host "[2/5] Installing WSL2 kernel update..." -ForegroundColor Yellow
if (-not (Test-Path $KernelMsi)) {
    Write-Host "  Downloading WSL2 kernel update..." -ForegroundColor Gray
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $KernelUrl -OutFile $KernelMsi -UseBasicParsing
}
Write-Host "  Running installer..." -ForegroundColor Gray
Start-Process msiexec.exe -ArgumentList "/i `"$KernelMsi`" /quiet /norestart" -Wait -NoNewWindow
Remove-Item $KernelMsi -Force -ErrorAction SilentlyContinue
Write-Host "  WSL2 kernel updated." -ForegroundColor Green

# Step 3: Set WSL2 as default
Write-Host "[3/5] Setting WSL2 as default version..." -ForegroundColor Yellow
wsl --set-default-version 2

# Step 4: Download Ubuntu 22.04 rootfs
Write-Host "[4/5] Downloading Ubuntu 22.04 rootfs..." -ForegroundColor Yellow
Write-Host "  URL: $DownloadUrl" -ForegroundColor Gray

if (Test-Path $TarFile) {
    $size = [math]::Round((Get-Item $TarFile).Length / 1MB, 1)
    Write-Host "  Already downloaded ($size MB), skipping." -ForegroundColor Green
} else {
    Write-Host "  Downloading... (about 220MB, please wait)" -ForegroundColor Yellow
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $DownloadUrl -OutFile $TarFile -UseBasicParsing
    $ProgressPreference = 'Continue'

    if (-not (Test-Path $TarFile)) {
        Write-Host "  ERROR: Download failed!" -ForegroundColor Red
        exit 1
    }
    $size = [math]::Round((Get-Item $TarFile).Length / 1MB, 1)
    Write-Host "  Done! ($size MB)" -ForegroundColor Green
}

# Step 5: Import into WSL
Write-Host "[5/5] Importing '$DistroName' to $WslDir ..." -ForegroundColor Yellow

$distroList = wsl --list --quiet 2>$null
$alreadyExists = $false
if ($distroList) {
    foreach ($line in $distroList) {
        if ($line -match "Ubuntu-22") { $alreadyExists = $true; break }
    }
}

if ($alreadyExists) {
    Write-Host "  $DistroName already registered. Skipping import." -ForegroundColor Green
} else {
    wsl --import $DistroName $WslDir $TarFile --version 2
    Write-Host "  Import complete." -ForegroundColor Green
}

wsl --set-default $DistroName

# Cleanup tar
Remove-Item $TarFile -Force -ErrorAction SilentlyContinue

# Done
Write-Host "`n==========================================" -ForegroundColor Green
Write-Host " DONE! Ubuntu 22.04 installed at $WslDir" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
wsl --list --verbose
Write-Host ""
Write-Host "Start with:  wsl -d $DistroName" -ForegroundColor Cyan
Write-Host ""
Write-Host "--- Create a non-root user ---" -ForegroundColor Yellow
Write-Host "  wsl -d $DistroName" -ForegroundColor Gray
Write-Host "  useradd -m -s /bin/bash yourname" -ForegroundColor Gray
Write-Host "  passwd yourname" -ForegroundColor Gray
Write-Host "  usermod -aG sudo yourname" -ForegroundColor Gray
Write-Host "  printf '[user]\ndefault=yourname\n' > /etc/wsl.conf" -ForegroundColor Gray
Write-Host "  exit" -ForegroundColor Gray
Write-Host "  wsl --shutdown" -ForegroundColor Gray
