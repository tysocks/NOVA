# Install the built NOVA onedir bundle for the current user and create shortcuts.
# Default install location: %LOCALAPPDATA%\Programs\NOVA (no admin required).

param(
    [string]$SourceDir = "",
    [string]$InstallDir = "",
    [switch]$NoDesktopShortcut,
    [switch]$NoStartMenuShortcut
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

if (-not $SourceDir) {
    $SourceDir = Join-Path $RepoRoot "dist\NOVA"
}
if (-not $InstallDir) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\NOVA"
}

$ExeName = "NOVA.exe"
$SourceExe = Join-Path $SourceDir $ExeName
if (-not (Test-Path $SourceExe)) {
    throw "Build missing: $SourceExe. Run packaging\build_windows.ps1 first."
}

Write-Host "Installing NOVA to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Robocopy mirrors the onedir tree; exit codes 0-7 are success.
& robocopy $SourceDir $InstallDir /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with exit code $LASTEXITCODE"
}

$TargetExe = Join-Path $InstallDir $ExeName
$IconPath = Join-Path $InstallDir "assets\NOVA_ICON.ico"
if (-not (Test-Path $IconPath)) {
    $IconPath = Join-Path $InstallDir "assets\NOVA_ICON.png"
}
if (-not (Test-Path $IconPath)) {
    $IconPath = $TargetExe
}

function New-Shortcut {
    param(
        [Parameter(Mandatory = $true)][string]$ShortcutPath,
        [Parameter(Mandatory = $true)][string]$Target,
        [string]$WorkingDirectory,
        [string]$IconLocation
    )
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($ShortcutPath)
    $sc.TargetPath = $Target
    $sc.WorkingDirectory = $WorkingDirectory
    if ($IconLocation) { $sc.IconLocation = "$IconLocation,0" }
    $sc.Description = "NOVA - Northern Operation Viewer and Analysis"
    $sc.Save()
}

if (-not $NoDesktopShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $desktopLnk = Join-Path $desktop "NOVA.lnk"
    New-Shortcut -ShortcutPath $desktopLnk -Target $TargetExe -WorkingDirectory $InstallDir -IconLocation $IconPath
    Write-Host "Desktop shortcut: $desktopLnk"
}

if (-not $NoStartMenuShortcut) {
    $startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    New-Item -ItemType Directory -Force -Path $startMenu | Out-Null
    $startLnk = Join-Path $startMenu "NOVA.lnk"
    New-Shortcut -ShortcutPath $startLnk -Target $TargetExe -WorkingDirectory $InstallDir -IconLocation $IconPath
    Write-Host "Start Menu shortcut: $startLnk"
}

# Uninstall helper for current-user install
$Uninstall = Join-Path $InstallDir "Uninstall-NOVA.ps1"
@"
`$ErrorActionPreference = 'Stop'
`$installDir = '$InstallDir'
`$desktop = [Environment]::GetFolderPath('Desktop')
`$start = Join-Path `$env:APPDATA 'Microsoft\Windows\Start Menu\Programs\NOVA.lnk'
Remove-Item -Force (Join-Path `$desktop 'NOVA.lnk') -ErrorAction SilentlyContinue
Remove-Item -Force `$start -ErrorAction SilentlyContinue
if (Test-Path `$installDir) { Remove-Item -Recurse -Force `$installDir }
Write-Host 'NOVA uninstalled.'
"@ | Set-Content -Path $Uninstall -Encoding UTF8

Write-Host "Installed. Launch: $TargetExe"
Write-Host "Uninstall: $Uninstall"
