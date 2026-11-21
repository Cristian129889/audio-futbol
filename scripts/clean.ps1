<#
.SYNOPSIS
    Cleans PyInstaller build artifacts, portable output folders, ZIP archives, and caches.
.DESCRIPTION
    Safely removes build/, dist/, AudioFutbol_Portable/, AudioFutbol_Portable.zip,
    *.spec, logs/, error.log, .pytest_cache/, and __pycache__/ without touching
    source code, game assets, config.json, or saves/.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$targets = @(
    "build",
    "dist",
    "AudioFutbol_Portable",
    "AudioFutbol_Portable.zip",
    "AudioFutbol.spec",
    "logs",
    "error.log",
    ".pytest_cache"
)

$removed = 0
foreach ($rel in $targets) {
    $full = Join-Path $RepoRoot $rel
    if (Test-Path $full) {
        Remove-Item -Path $full -Recurse -Force
        Write-Host "Removed: $rel"
        $removed++
    }
}

Get-ChildItem -Path $RepoRoot -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Remove-Item -Path $_.FullName -Recurse -Force
        $removed++
    }

Get-ChildItem -Path (Join-Path $RepoRoot "src") -Directory -Filter "*.egg-info" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Remove-Item -Path $_.FullName -Recurse -Force
        Write-Host "Removed: src\$($_.Name)"
        $removed++
    }

Write-Host "Cleanup complete ($removed items removed)."
