# HEAXHub Windows Worker Agent — service uninstaller

param(
    [string]$ServiceName = "HEAXHubAgent"
)

$ErrorActionPreference = "Stop"

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Write-Host "Stopping $ServiceName ..."
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Write-Host "Deleting $ServiceName ..."
    sc.exe delete $ServiceName | Out-Null
    Write-Host "Removed."
} else {
    Write-Host "Service $ServiceName not installed."
}
