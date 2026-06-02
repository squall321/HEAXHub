# HEAXHub Windows Worker Agent — service installer
#
# 사용법:
#   1. dotnet publish -c Release -r win-x64 --self-contained=true -p:PublishSingleFile=true
#   2. publish 디렉터리를 C:\Program Files\HEAXHub\Agent 로 복사
#   3. 본 스크립트를 관리자 PowerShell 로 실행
#
# 환경 변수는 시스템 환경 변수로 설정한다.
#   HEAX_HUB_URL       (예: https://hub.company.com)
#   HEAX_AGENT_TOKEN   (등록 시 받은 plaintext token)
#   HEAX_AGENT_POOL    (예: windows-cae-tools)

param(
    [string]$InstallDir = "C:\Program Files\HEAXHub\Agent",
    [string]$ServiceName = "HEAXHubAgent",
    [string]$DisplayName = "HEAXHub Windows Worker Agent",
    [string]$Description = "Polls HEAXHub for Windows GUI/CLI jobs and reports results."
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $InstallDir)) {
    throw "Install directory not found: $InstallDir"
}

$exePath = Join-Path $InstallDir "HeaxAgent.exe"
if (-not (Test-Path $exePath)) {
    throw "HeaxAgent.exe not found in $InstallDir. Did you run dotnet publish and copy the output?"
}

# Stop + remove any prior service.
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Write-Host "Stopping existing service $ServiceName ..."
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    sc.exe delete $ServiceName | Out-Null
    Start-Sleep -Seconds 2
}

Write-Host "Creating service $ServiceName -> $exePath"
New-Service -Name $ServiceName `
            -BinaryPathName "`"$exePath`"" `
            -DisplayName $DisplayName `
            -Description $Description `
            -StartupType Automatic | Out-Null

# Best-effort: log directory.
$logDir = "C:\ProgramData\HEAXHub"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

Write-Host ""
Write-Host "Configure environment (system-wide) before starting the service:"
Write-Host "  [Environment]::SetEnvironmentVariable('HEAX_HUB_URL', 'https://hub.company.com', 'Machine')"
Write-Host "  [Environment]::SetEnvironmentVariable('HEAX_AGENT_TOKEN', '<one-time token>', 'Machine')"
Write-Host "  [Environment]::SetEnvironmentVariable('HEAX_AGENT_POOL', 'windows-cae-tools', 'Machine')"
Write-Host ""
Write-Host "Then start with:  Start-Service $ServiceName"
