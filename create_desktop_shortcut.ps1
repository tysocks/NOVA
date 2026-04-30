$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $projectRoot "Launch_NOVA.vbs"
$icon = Join-Path $projectRoot "assets\NOVA_ICON.png"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "NOVA.lnk"
$startMenuPrograms = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$startMenuShortcutPath = Join-Path $startMenuPrograms "NOVA.lnk"

$wsh = New-Object -ComObject WScript.Shell
function New-NovaShortcut([string]$path) {
  $shortcut = $wsh.CreateShortcut($path)
  $shortcut.TargetPath = $target
  $shortcut.WorkingDirectory = $projectRoot
  if (Test-Path $icon) {
    $shortcut.IconLocation = $icon
  } else {
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
  }
  $shortcut.Description = "NOVA - Northern Operation Viewer and Analysis"
  $shortcut.Save()
}

New-NovaShortcut $shortcutPath
New-NovaShortcut $startMenuShortcutPath
Write-Output "Created shortcut: $shortcutPath"
Write-Output "Created shortcut: $startMenuShortcutPath"
