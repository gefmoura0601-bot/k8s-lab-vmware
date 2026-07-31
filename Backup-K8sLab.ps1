param(
    [switch]$RestartAfterBackup,
    [string]$ProjectRoot = $PSScriptRoot,
    [string]$BackupRoot = "F:\Backups\k8s-lab-vmware"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$VagrantPath = Join-Path $ProjectRoot "iac\vagrant"

function Write-Step($msg) {
    Write-Host "`n==== $msg ====" -ForegroundColor Cyan
}

function Ensure-Path($path) {
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Path $path | Out-Null
    }
}

$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$BackupPath = Join-Path $BackupRoot $timestamp
$BackupProjectPath = Join-Path $BackupPath "k8s-vmware"
$ManifestPath = Join-Path $BackupPath "backup-manifest.json"

Ensure-Path $BackupRoot
Ensure-Path $BackupPath

Write-Step "Parando o lab via Vagrant"
Push-Location $VagrantPath
try {
    vagrant halt
}
finally {
    Pop-Location
}

Write-Step "Aguardando 10 segundos para flush de disco"
Start-Sleep -Seconds 10

Write-Step "Copiando projeto completo com VMs"
robocopy $ProjectRoot $BackupProjectPath /MIR /R:2 /W:2 /NFL /NDL /NP /XF "*.log" /XD "*.lck"
$rc = $LASTEXITCODE
if ($rc -ge 8) {
    throw "Robocopy falhou. ExitCode=$rc"
}

Write-Step "Gerando manifesto"
$manifest = [ordered]@{
    Timestamp   = $timestamp
    ProjectRoot = $ProjectRoot
    VagrantPath = $VagrantPath
    BackupRoot  = $BackupRoot
    RestoreNote = "Restaure preferencialmente para o mesmo caminho original: C:\Labs\k8s-vmware"
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -Path $ManifestPath -Encoding UTF8

Write-Step "Backup concluído"
Write-Host "Backup salvo em: $BackupPath" -ForegroundColor Green

if ($RestartAfterBackup) {
    Write-Step "Subindo o lab novamente"
    Push-Location $VagrantPath
    try {
        vagrant up
    }
    finally {
        Pop-Location
    }
}
