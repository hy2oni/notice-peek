param(
    [string]$ProgramDirectory = (Join-Path $PSScriptRoot 'dist\notice-peek'),
    [string]$ShortcutDirectory = [Environment]::GetFolderPath('Desktop')
)

$ErrorActionPreference = 'Stop'
$programPath = (Resolve-Path -LiteralPath $ProgramDirectory).Path
$exePath = Join-Path $programPath 'notice-peek.exe'
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) { throw "EXE not found: $exePath" }
if (-not (Test-Path -LiteralPath $ShortcutDirectory -PathType Container)) {
    throw "Shortcut directory not found: $ShortcutDirectory"
}
$shortcutPath = Join-Path (Resolve-Path -LiteralPath $ShortcutDirectory).Path 'notice-peek.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.TargetPath -ne $exePath) {
    throw 'An unrelated notice-peek shortcut already exists. It was not replaced.'
}
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = $programPath
$shortcut.IconLocation = "$exePath,0"
$shortcut.Description = 'notice-peek'
$shortcut.WindowStyle = 1
$shortcut.Save()
Write-Output "Shortcut: $shortcutPath"
