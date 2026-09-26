# AntiAgent 1-Line Installer for Windows
# Usage in PowerShell:
#   irm https://raw.githubusercontent.com/aiden-guan/AntiAgent/main/install.ps1 | iex

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  🛡️ Installing AntiAgent Guard for Windows (Public Beta)..." -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Verify Python Installation
$pythonCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCmd = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
}

if (-not $pythonCmd) {
    Write-Host "❌ Python 3.9+ was not found on your system!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please install Python 3.9+ by running:" -ForegroundColor Yellow
    Write-Host "    winget install Python.Python.3.12" -ForegroundColor White
    Write-Host "Or download it from https://www.python.org/downloads/" -ForegroundColor White
    Write-Host ""
    exit 1
}

$pyVer = & $pythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "🐍 Detected Python $pyVer ($pythonCmd) [OK]" -ForegroundColor Green

# 2. Setup AntiAgent Directory
$installDir = Join-Path $HOME ".antiagent"
if (-not (Test-Path $installDir)) {
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
}

# 3. Install AntiAgent via pip
Write-Host "⬇️ Installing AntiAgent package..." -ForegroundColor Cyan
# pip writes warnings to stderr; under "Stop", Windows PowerShell 5.1 turns that into
# a terminating error and aborts the installer before the hook is registered.
$ErrorActionPreference = "Continue"
& $pythonCmd -m pip install --upgrade antiagent 2>$null
if ($LASTEXITCODE -ne 0) {
    # Fallback to the GitHub source archive (does not require git to be installed)
    Write-Host "ℹ️ Installing from GitHub repository..." -ForegroundColor Yellow
    & $pythonCmd -m pip install --upgrade "https://github.com/aiden-guan/AntiAgent/archive/refs/heads/main.zip"
}
& $pythonCmd -c "import antiagent" 2>$null
$importOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"
if (-not $importOk) {
    Write-Host "❌ AntiAgent could not be installed into Python $pyVer ($pythonCmd)." -ForegroundColor Red
    Write-Host "   Try manually: $pythonCmd -m pip install git+https://github.com/aiden-guan/AntiAgent.git" -ForegroundColor Yellow
    exit 1
}

# 4. Register Global Protection Hook in Antigravity
Write-Host "🌐 Registering Global Antigravity Hook..." -ForegroundColor Cyan
& $pythonCmd -m antiagent install --global

# 5. Create Desktop Launcher & Shortcut
$batPath = Join-Path $installDir "AntiAgent.bat"
$batContent = @"
@echo off
title AntiAgent Guard
$pythonCmd -m antiagent app
"@
Set-Content -Path $batPath -Value $batContent -Encoding UTF8

$desktopPath = [System.IO.Path]::Combine($HOME, "Desktop")
if (Test-Path $desktopPath) {
    try {
        $wscript = New-Object -ComObject WScript.Shell
        $shortcut = $wscript.CreateShortcut("$desktopPath\AntiAgent Guard.lnk")
        $shortcut.TargetPath = $batPath
        $shortcut.Description = "AntiAgent Guard for Google Antigravity"
        $shortcut.Save()
        Write-Host "📌 Created Desktop Shortcut: 'AntiAgent Guard'" -ForegroundColor Green
    } catch {
        # Continue silently if shortcut creation fails
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  ✅ AntiAgent Guard successfully installed!" -ForegroundColor Green
Write-Host "  Google Antigravity is now protected across all your projects." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""

# 6. Launch Desktop Application
Write-Host "🚀 Launching AntiAgent Guard..." -ForegroundColor Cyan
& $pythonCmd -m antiagent app
