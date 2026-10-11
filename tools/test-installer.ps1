param([switch]$OpenApp, [string]$CompilerPath)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$testDir = Join-Path $projectDir ('.local\installer-check-' + [guid]::NewGuid().ToString('N'))
& (Join-Path $projectDir 'package.ps1') -SkipBuild -CompilerPath $CompilerPath -TestRoot $testDir
$setup = Join-Path $testDir 'output\notice-peek-setup-test.exe'
$programDir = Join-Path $testDir 'program'
$exe = Join-Path $programDir 'notice-peek.exe'
$sourceDir = Join-Path $projectDir 'dist\develop\notice-peek'
$dataDir = Join-Path $testDir 'app-data'
$report = [ordered]@{ testDirectory = $testDir }
function Invoke-TestInstall {
    $process = Start-Process -FilePath $setup -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOCLOSEAPPLICATIONS') -WindowStyle Hidden -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw "Test installation failed: $($process.ExitCode)" }
}
Invoke-TestInstall
$sourceFiles = @(Get-ChildItem -LiteralPath $sourceDir -Recurse -File)
foreach ($file in $sourceFiles) {
    $relative = $file.FullName.Substring($sourceDir.Length + 1)
    $installed = Join-Path $programDir $relative
    if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) { throw "Missing installed file: $relative" }
    if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath $installed).Hash) {
        throw "Installed file differs: $relative"
    }
}
$report.payloadFilesVerified = $sourceFiles.Count
foreach ($required in @('licenses\Python-LICENSE.txt','THIRD_PARTY_NOTICES.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $programDir $required) -PathType Leaf)) {
        throw "Distribution notice missing: $required"
    }
}
$report.distributionNoticesVerified = 2
$shell = New-Object -ComObject WScript.Shell
foreach ($location in @('desktop','start-menu')) {
    $link = Join-Path $testDir "shortcuts\$location\notice-peek.lnk"
    if (-not (Test-Path -LiteralPath $link)) { throw "Shortcut missing: $location" }
    $shortcut = $shell.CreateShortcut($link)
    if ($shortcut.TargetPath -ne $exe -or $shortcut.WorkingDirectory -ne $programDir) {
        throw "Shortcut target is invalid: $location"
    }
}
$report.shortcutsVerified = 2
New-Item -ItemType Directory -Force -Path (Join-Path $dataDir 'notice-peek') | Out-Null
$db = Join-Path $dataDir 'notice-peek\notices.sqlite3'
$python = Join-Path $projectDir '.venv\Scripts\python.exe'
Push-Location -LiteralPath $projectDir
try {
    & $python -c 'import sys; from notice_peek.storage import Store; from notice_peek.models import Notice; s=Store(sys.argv[1]); s.subscribe("cse"); s.save_collection("cse",[Notice("cse","test-record","설치 보존 검증",None,"https://www.yu.ac.kr/cse/community/notice.do")]); s.set_read("cse","test-record",False); s.set_favorite("cse","test-record",True)' $db
    if ($LASTEXITCODE -ne 0) { throw 'Test database creation failed.' }
} finally { Pop-Location }
if ($OpenApp) {
    $oldLocal = $env:LOCALAPPDATA
    $oldPath = $env:PATH
    $oldPythonHome = $env:PYTHONHOME
    $oldPythonPath = $env:PYTHONPATH
    try {
        $env:LOCALAPPDATA = $dataDir
        $env:PATH = Join-Path $env:SystemRoot 'System32'
        $env:PYTHONHOME = $null
        $env:PYTHONPATH = $null
        $appProcess = Start-Process -FilePath $exe -WorkingDirectory $testDir -PassThru
        Write-Output "Installed test app PID: $($appProcess.Id)"
        if (-not $appProcess.WaitForExit(180000)) { throw 'Close the installed test app before continuing.' }
        if ($appProcess.ExitCode -ne 0) { throw 'Installed app did not exit normally.' }
        $report.appExitCode = $appProcess.ExitCode
    } finally {
        $env:LOCALAPPDATA = $oldLocal
        $env:PATH = $oldPath
        $env:PYTHONHOME = $oldPythonHome
        $env:PYTHONPATH = $oldPythonPath
    }
}
$dbHash = (Get-FileHash -LiteralPath $db).Hash
Invoke-TestInstall
if ((Get-FileHash -LiteralPath $db).Hash -ne $dbHash) { throw 'Upgrade changed the database.' }
$report.upgradePreservedDatabase = $true
$uninstaller = Join-Path $programDir 'unins000.exe'
$uninstall = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -WindowStyle Hidden -PassThru -Wait
if ($uninstall.ExitCode -ne 0) { throw 'Test uninstall failed.' }
if (Test-Path -LiteralPath $exe) { throw 'Program survived uninstall.' }
foreach ($location in @('desktop','start-menu')) {
    if (Test-Path -LiteralPath (Join-Path $testDir "shortcuts\$location\notice-peek.lnk")) { throw 'Shortcut survived uninstall.' }
}
if ((Get-FileHash -LiteralPath $db).Hash -ne $dbHash) { throw 'Uninstall changed the database.' }
$report.uninstallPreservedDatabase = $true
$report.uninstallRemovedProgramAndShortcuts = $true
$report | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $testDir 'result.json') -Encoding utf8
$report | ConvertTo-Json
