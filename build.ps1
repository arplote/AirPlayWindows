<#
.SYNOPSIS
    Script de génération du package autonome AirPlaySender pour Windows 11.
.DESCRIPTION
    Phase 8 du projet AirPlayWindows.
    Construit l'exécutable autonome (onedir) avec PyInstaller.
.PARAMETER DebugConsole
    Génère l'exécutable avec console apparente (pour debug/dépannage).
#>

param(
    [switch]$DebugConsole
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " FABRICATION DU PACKAGE AIRPLAY SENDER (PyInstaller)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$PythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Environnement virtuel introuvable dans .venv ! Exécutez d'abord l'installation."
    exit 1
}

# 1. Génération de l'icône si absente
$IconPath = Join-Path $PSScriptRoot "assets\icon.ico"
if (-not (Test-Path $IconPath)) {
    Write-Host "[1/3] Génération de l'icône multi-résolution..." -ForegroundColor Yellow
    & $PythonExe -c "from pathlib import Path; from airplay_sender.icons import save_ico_file; save_ico_file(Path('assets/icon.ico'))"
} else {
    Write-Host "[1/3] Icône assets/icon.ico présente." -ForegroundColor Green
}

# 2. Préparation des arguments PyInstaller
Write-Host "[2/3] Configuration des paramètres PyInstaller..." -ForegroundColor Yellow

$ConsoleFlag = if ($DebugConsole) { "--console" } else { "--noconsole" }

$PyInstallerArgs = @(
    "-m", "PyInstaller",
    "--name", "AirPlaySender",
    "--onedir",
    $ConsoleFlag,
    "--icon", $IconPath,
    "--paths", "src",
    "--collect-data", "pyatv",
    "--collect-data", "zeroconf",
    "--collect-binaries", "pyaudiowpatch",
    "--collect-binaries", "soxr",
    "--hidden-import", "pyaudiowpatch",
    "--hidden-import", "soxr",
    "--hidden-import", "pyatv",
    "--hidden-import", "zeroconf",
    "--hidden-import", "pystray",
    "--hidden-import", "pystray._win32",
    "--hidden-import", "PIL",
    "--hidden-import", "pydantic",
    "--hidden-import", "cryptography",
    "--hidden-import", "srptools",
    "--hidden-import", "miniaudio",
    "--hidden-import", "tinytag",
    "--clean",
    "--noconfirm",
    "src/airplay_sender/__main__.py"
)

# 3. Lancement de la compilation
Write-Host "[3/3] Exécution de PyInstaller ($ConsoleFlag)..." -ForegroundColor Yellow
& $PythonExe $PyInstallerArgs

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n[SUCCÈS] Package créé dans : dist\AirPlaySender\" -ForegroundColor Green
    Write-Host "Exécutable : dist\AirPlaySender\AirPlaySender.exe" -ForegroundColor Green
} else {
    Write-Error "`n[ÉCHEC] Erreur lors de la compilation PyInstaller."
    exit $LASTEXITCODE
}
