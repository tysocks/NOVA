$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $projectRoot "Launch_NOVA.vbs"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "NOVA.lnk"

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$shortcut.Description = "NOVA - Northern Operation Viewer and Analysis"
$shortcut.Save()

Write-Output "Created shortcut: $shortcutPath"
