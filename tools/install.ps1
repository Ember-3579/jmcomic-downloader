param(
    [string]$SourceDir = "",
    [string]$InstallDir = "",
    [switch]$NoDesktopShortcut,
    [switch]$NoShortcuts,
    [switch]$NoRegistry,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"

if (-not $SourceDir) {
    $SourceDir = Join-Path (Split-Path -Parent $PSScriptRoot) "dist\JMComicDownloader"
}
if (-not $InstallDir) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\JMComicDownloader"
}
$SourceExe = Join-Path $SourceDir "JMComicDownloader.exe"
if (-not (Test-Path -LiteralPath $SourceExe)) {
    throw "找不到程序文件：$SourceExe"
}

$SourceDir = (Resolve-Path -LiteralPath $SourceDir).Path
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
$Exe = Join-Path $InstallDir "JMComicDownloader.exe"

Get-Process -Name "JMComicDownloader" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $Exe } |
    Stop-Process -Force -ErrorAction SilentlyContinue

if (Test-Path -LiteralPath $InstallDir) {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

Copy-Item -Path (Join-Path $SourceDir "*") -Destination $InstallDir -Recurse -Force

$DocRoot = Split-Path -Parent $PSScriptRoot
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot "README.md")) {
    $DocRoot = $PSScriptRoot
}
foreach ($name in @("README.md", "THIRD_PARTY_NOTICES.md", "LICENSE")) {
    $source = Join-Path $DocRoot $name
    if (Test-Path -LiteralPath $source) {
        $targetName = if ($name -eq "README.md") { "README.txt" } else { $name }
        Copy-Item -LiteralPath $source -Destination (Join-Path $InstallDir $targetName) -Force
    }
}

$UninstallPs1 = @'
param([switch]$PurgeData)
$ErrorActionPreference = "SilentlyContinue"
$InstallDir = $PSScriptRoot
$Exe = Join-Path $InstallDir "JMComicDownloader.exe"

Get-Process -Name "JMComicDownloader" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $Exe } |
    Stop-Process -Force -ErrorAction SilentlyContinue

$desktop = [Environment]::GetFolderPath("DesktopDirectory")
$programs = [Environment]::GetFolderPath("Programs")
Remove-Item -LiteralPath (Join-Path $desktop "JM 漫画下载器.lnk") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $programs "JM 漫画下载器.lnk") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\JMComicDownloader" -Recurse -Force -ErrorAction SilentlyContinue

if ($PurgeData) {
    $data = Join-Path $env:LOCALAPPDATA "JMComicDownloader"
    Remove-Item -LiteralPath $data -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "已删除程序与本地设置/历史记录。下载的漫画不会被删除。" -ForegroundColor Yellow
} else {
    Write-Host "已删除程序。设置与历史记录保留在 %LOCALAPPDATA%\JMComicDownloader，下载的漫画不会被删除。" -ForegroundColor Yellow
}

$target = $InstallDir
Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-Command", "Start-Sleep -Milliseconds 900; Remove-Item -LiteralPath '$target' -Recurse -Force"
)
'@

$UninstallPs1Path = Join-Path $InstallDir "Uninstall.ps1"
[System.IO.File]::WriteAllText(
    $UninstallPs1Path,
    $UninstallPs1,
    (New-Object System.Text.UTF8Encoding($true))
)

$UninstallCmd = @'
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Uninstall.ps1"
'@
[System.IO.File]::WriteAllText(
    (Join-Path $InstallDir "Uninstall.cmd"),
    $UninstallCmd,
    (New-Object System.Text.UTF8Encoding($true))
)

function New-AppShortcut([string]$Path) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $Exe
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.IconLocation = "$Exe,0"
    $shortcut.Description = "JM 漫画下载器 - 本地漫画下载器"
    $shortcut.Save()
}

if (-not $NoShortcuts) {
    $desktop = [Environment]::GetFolderPath("DesktopDirectory")
    $programs = [Environment]::GetFolderPath("Programs")
    if (-not $NoDesktopShortcut) {
        New-AppShortcut (Join-Path $desktop "JM 漫画下载器.lnk")
    }
    New-AppShortcut (Join-Path $programs "JM 漫画下载器.lnk")
}

if (-not $NoRegistry) {
    $uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\JMComicDownloader"
    New-Item -Path $uninstallKey -Force | Out-Null
    Set-ItemProperty -Path $uninstallKey -Name "DisplayName" -Value "JM 漫画下载器"
    Set-ItemProperty -Path $uninstallKey -Name "DisplayVersion" -Value "1.0.0"
    Set-ItemProperty -Path $uninstallKey -Name "Publisher" -Value "Local build"
    Set-ItemProperty -Path $uninstallKey -Name "InstallLocation" -Value $InstallDir
    Set-ItemProperty -Path $uninstallKey -Name "DisplayIcon" -Value "$Exe,0"
    Set-ItemProperty -Path $uninstallKey -Name "UninstallString" -Value "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\Uninstall.ps1`""
    Set-ItemProperty -Path $uninstallKey -Name "QuietUninstallString" -Value "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\Uninstall.ps1`""
    Set-ItemProperty -Path $uninstallKey -Name "InstallDate" -Value (Get-Date -Format "yyyyMMdd")
    Set-ItemProperty -Path $uninstallKey -Name "EstimatedSize" -Value ([int]((Get-Item -LiteralPath $Exe).Length / 1KB)) -Type DWord
    Set-ItemProperty -Path $uninstallKey -Name "NoModify" -Value 1 -Type DWord
    Set-ItemProperty -Path $uninstallKey -Name "NoRepair" -Value 1 -Type DWord
}

Write-Host "已安装到：$InstallDir" -ForegroundColor Green
if (-not $NoShortcuts) {
    Write-Host "开始菜单和桌面快捷方式已创建：JM 漫画下载器" -ForegroundColor Green
}
if (-not $NoLaunch) {
    Start-Process -FilePath $Exe -WorkingDirectory $InstallDir
}
