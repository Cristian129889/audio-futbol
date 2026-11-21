<#
.SYNOPSIS
    Creates/prepares the Python environment (Conda 'futbol' or local .venv), runs tests,
    and compiles the no-console portable Windows build of AudioFutbol.
.PARAMETER UseVenv
    Force creating/using a local Python virtual environment (.venv) instead of Conda.
.PARAMETER SkipTests
    Skip running pytest before building the executable.
.PARAMETER CleanAfter
    Remove temporary build/, dist/, and AudioFutbol.spec after generating AudioFutbol_Portable.zip.
#>
[CmdletBinding()]
param(
    [switch]$UseVenv,
    [switch]$SkipTests,
    [switch]$CleanAfter
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $RepoRoot

try {
    $pythonExe = $null

    if (-not $UseVenv -and (Get-Command conda -ErrorAction SilentlyContinue)) {
        Write-Host "==> Setting up Conda environment 'futbol' (Python 3.12)..."
        $envLine = (conda env list) | Select-String -Pattern '^\s*futbol\s+'
        if (-not $envLine) {
            conda create -n futbol python=3.12 -y
            if ($LASTEXITCODE -ne 0) { throw "Failed to create Conda environment 'futbol'." }
            $envLine = (conda env list) | Select-String -Pattern '^\s*futbol\s+'
        }
        $envPath = ($envLine.ToString().Trim() -split '\s+')[-1]
        $pythonExe = Join-Path $envPath "python.exe"
        if (-not (Test-Path $pythonExe)) {
            throw "Could not locate python.exe inside Conda environment at: $envPath"
        }
    }
    else {
        Write-Host "==> Setting up local virtual environment '.venv'..."
        $venvDir = Join-Path $RepoRoot ".venv"
        $pythonExe = Join-Path $venvDir "Scripts\python.exe"
        if (-not (Test-Path $pythonExe)) {
            $sysPy = Get-Command python -ErrorAction SilentlyContinue
            if (-not $sysPy) { $sysPy = Get-Command py -ErrorAction SilentlyContinue }
            if (-not $sysPy) { throw "Python 3.10+ not found on PATH." }
            & $sysPy.Source -m venv $venvDir
            if ($LASTEXITCODE -ne 0) { throw "Failed to create .venv." }
        }
    }

    Write-Host "==> Using Python: $pythonExe"
    Write-Host "==> Installing dependencies and editable package..."
    & $pythonExe -m pip install --upgrade pip
    & $pythonExe -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

    if (-not $SkipTests) {
        Write-Host "==> Running automated test suite..."
        & $pythonExe -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "Tests failed; aborting build." }
    }

    Write-Host "==> Building no-console portable executable..."
    & $pythonExe (Join-Path $RepoRoot "scripts\build_exe.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    if ($CleanAfter) {
        foreach ($temp in @("build", "dist", "AudioFutbol.spec")) {
            $p = Join-Path $RepoRoot $temp
            if (Test-Path $p) { Remove-Item -Path $p -Recurse -Force }
        }
    }

    Write-Host ""
    Write-Host "Build succeeded!"
    Write-Host "  Portable folder: $(Join-Path $RepoRoot 'AudioFutbol_Portable')"
    Write-Host "  Portable archive: $(Join-Path $RepoRoot 'AudioFutbol_Portable.zip')"
}
finally {
    Pop-Location
}
