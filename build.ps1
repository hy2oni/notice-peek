param([switch]$CreateShortcut)

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$pythonPath = Join-Path $projectDir '.venv\Scripts\python.exe'
$outputDir = Join-Path $projectDir 'dist\notice-peek'
$exePath = Join-Path $outputDir 'notice-peek.exe'
$assetDir = Join-Path $projectDir 'notice_peek\assets'
$iconPath = Join-Path $assetDir 'notice-peek.ico'

if (Get-Process -Name 'notice-peek' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exePath }) {
    throw 'Close notice-peek before building a new version.'
}
Push-Location -LiteralPath $projectDir
try {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the build environment.' }
    }
    & $pythonPath -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Could not install build dependencies.' }
    & $pythonPath -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed. Build stopped.' }
    & $pythonPath -m PyInstaller --noconfirm --onedir --windowed --noupx `
        --name notice-peek --distpath dist --workpath build\pyinstaller --specpath build `
        --icon $iconPath `
        --add-data "$assetDir;notice_peek/assets" run_notice_peek.pyw
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
    if (-not (Test-Path -LiteralPath $exePath)) { throw 'The expected EXE was not created.' }
    if ($CreateShortcut) { & (Join-Path $projectDir 'create-shortcut.ps1') -ProgramDirectory $outputDir }
    Write-Output "Built: $exePath"
}
finally {
    Pop-Location
}
