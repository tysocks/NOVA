# Build NOVA Windows onedir bundle + zip, then optionally install shortcuts.
# Usage (from repo root):
#   .\packaging\build_windows.ps1
#   .\packaging\build_windows.ps1 -Install

param(
    [switch]$Install,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "Missing venv at .venv. Create it and install backend/requirements.txt + pyinstaller first."
}

$DistDir = Join-Path $RepoRoot "dist\NOVA"
$OutDir = Join-Path $RepoRoot "packaging\output"
$Version = "0.0.0"
$ZipPath = Join-Path $OutDir "NOVA-$Version-windows-x64.zip"

# Ensure PyInstaller is available
& $VenvPython -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $VenvPython -m pip install pyinstaller
}

# Build .ico from PNG when Pillow is available (shortcuts prefer .ico)
$Png = Join-Path $RepoRoot "assets\NOVA_ICON.png"
$Ico = Join-Path $RepoRoot "assets\NOVA_ICON.ico"
if ((Test-Path $Png) -and -not (Test-Path $Ico)) {
    & $VenvPython -c @"
from pathlib import Path
try:
    from PIL import Image
except ImportError:
    raise SystemExit(0)
png = Path(r'$Png')
ico = Path(r'$Ico')
img = Image.open(png).convert('RGBA')
sizes = [(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]
img.save(ico, sizes=sizes)
print('Wrote', ico)
"@
}

if (-not $SkipBuild) {
    Write-Host "Building NOVA with PyInstaller (onedir)..."
    & $VenvPython -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath (Join-Path $RepoRoot "dist") `
        --workpath (Join-Path $RepoRoot "build\pyinstaller") `
        (Join-Path $PSScriptRoot "nova.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
}

if (-not (Test-Path (Join-Path $DistDir "NOVA.exe"))) {
    throw "Expected dist\NOVA\NOVA.exe was not produced."
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path (Join-Path $DistDir "*") -DestinationPath $ZipPath -Force
Write-Host "Created $ZipPath"

if ($Install) {
    & (Join-Path $PSScriptRoot "Install-NOVA.ps1")
}

Write-Host "Done. Bundle: $DistDir"
Write-Host "Zip:      $ZipPath"
