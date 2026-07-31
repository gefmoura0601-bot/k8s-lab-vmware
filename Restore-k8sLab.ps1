param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFolder,
    [string]$RestoreTarget = $PSScriptRoot,
    [switch]$Mirror
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host "`n==== $msg ====" -ForegroundColor Yellow
}

$RestoreTarget = [System.IO.Path]::GetFullPath($RestoreTarget)
$RestoreVagrantPath = Join-Path $RestoreTarget "iac\vagrant"
$SourceProjectPath = Join-Path $BackupFolder "k8s-vmware"

if (-not (Test-Path $SourceProjectPath)) {
    throw "Backup não encontrado em: $SourceProjectPath"
}

Write-Step "Restaurando projeto completo"
$copyMode = if ($Mirror) { "/MIR" } else { "/E" }
robocopy $SourceProjectPath $RestoreTarget $copyMode /R:2 /W:2 /NFL /NDL /NP
$rc = $LASTEXITCODE
if ($rc -ge 8) {
    throw "Robocopy falhou no restore. ExitCode=$rc"
}

Write-Step "Subindo o lab"
Push-Location $RestoreVagrantPath
try {
    vagrant up k8s-master --provision
    vagrant up k8s-worker-01 --provision
    vagrant up k8s-worker-02 --provision
}
finally {
    Pop-Location
}

Write-Step "Restore concluído"
