param(
    [switch]$SkipSelfTest,
    [switch]$Console
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root "build\venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Build environment is missing. Run: python -m venv build\venv; build\venv\Scripts\python.exe -m pip install -r requirements-build.txt"
}

if ($Console) {
    $env:JM_BUILD_CONSOLE = "1"
} else {
    Remove-Item Env:\JM_BUILD_CONSOLE -ErrorAction SilentlyContinue
}

Write-Host "Building JMComicDownloader..." -ForegroundColor Cyan
& $Python -m PyInstaller --noconfirm --clean build.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$Name = if ($Console) { "JMComicDebug" } else { "JMComicDownloader" }
$Exe = Join-Path $Root "dist\$Name\$Name.exe"
if (-not (Test-Path $Exe)) {
    throw "Expected output was not created: $Exe"
}

$FolderSize = (Get-ChildItem (Split-Path -Parent $Exe) -Recurse -File | Measure-Object -Property Length -Sum).Sum
$SizeMb = [math]::Round($FolderSize / 1MB, 1)
Write-Host "Built $Exe ($SizeMb MB total)" -ForegroundColor Green

if (-not $SkipSelfTest) {
    Write-Host "Running packaged self-test..." -ForegroundColor Cyan
    $Process = Start-Process -FilePath $Exe -ArgumentList "--self-test" -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        throw "Packaged self-test failed with exit code $($Process.ExitCode)"
    }
    Write-Host "Packaged self-test passed." -ForegroundColor Green
}

exit 0
