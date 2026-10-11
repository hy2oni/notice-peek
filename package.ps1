param(
    [switch]$SkipBuild,
    [string]$CompilerPath,
    [string]$TestRoot
)

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$programDir = Join-Path $projectDir 'dist\develop\notice-peek'
$outputDir = Join-Path $projectDir 'dist\release'
if (-not $CompilerPath) {
    $candidates = @(
        (Join-Path $projectDir '.local\toolchain\inno-6.7.3\ISCC.exe'),
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe'
    )
    $CompilerPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $CompilerPath -or -not (Test-Path -LiteralPath $CompilerPath -PathType Leaf)) {
    throw 'Inno Setup 6.7.3 or later is required. Supply -CompilerPath pointing to ISCC.exe.'
}
if ($TestRoot) {
    $localRoot = [IO.Path]::GetFullPath((Join-Path $projectDir '.local')) + [IO.Path]::DirectorySeparatorChar
    $TestRoot = [IO.Path]::GetFullPath($TestRoot)
    if (-not $TestRoot.StartsWith($localRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Installer test files must stay under the project .local directory.'
    }
    $outputDir = Join-Path $TestRoot 'output'
}
if (-not $SkipBuild) { & (Join-Path $projectDir 'build.ps1') -Development }
$exePath = Join-Path $programDir 'notice-peek.exe'
$catalogPath = Join-Path $programDir '_internal\notice_peek\assets\board_catalog.json'
foreach ($required in @($exePath, $catalogPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing build file: $required" }
}
$versionText = Get-Content -LiteralPath (Join-Path $projectDir 'notice_peek\__init__.py') -Raw
if ($versionText -notmatch '__version__\s*=\s*"(\d+\.\d+\.\d+)"') { throw 'Application version is missing.' }
$appVersion = $Matches[1]
$python = Join-Path $projectDir '.venv\Scripts\python.exe'
$pythonBase = & $python -c 'import sys; print(sys.base_prefix)'
if ($LASTEXITCODE -ne 0) { throw 'Could not locate the bundled Python license.' }
$pythonLicense = Join-Path $pythonBase 'LICENSE.txt'
if (-not (Test-Path -LiteralPath $pythonLicense -PathType Leaf)) { throw 'Python license was not found.' }
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
$arguments = @('/Qp', "/DAppVersion=$appVersion", "/DProgramDirectory=$programDir", "/DPythonLicenseFile=$pythonLicense", "/DOutputDirectory=$outputDir")
if ($TestRoot) { $arguments += "/DTestRoot=$TestRoot" }
$arguments += Join-Path $projectDir 'installer\notice-peek.iss'
& $CompilerPath @arguments
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
$fileName = if ($TestRoot) { 'notice-peek-setup-test.exe' } else { 'notice-peek-setup.exe' }
$setupPath = Join-Path $outputDir $fileName
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) { throw 'Installer output was not found.' }
$hash = (Get-FileHash -LiteralPath $setupPath -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  $fileName" | Set-Content -LiteralPath (Join-Path $outputDir 'SHA256SUMS.txt') -Encoding ascii
Write-Output "Installer: $setupPath"
