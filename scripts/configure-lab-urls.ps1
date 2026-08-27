[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateScript({
        $parts = $_.Split('.')
        $parts.Count -eq 4 -and -not ($parts | Where-Object { $_ -notmatch '^\d{1,3}$' -or [int]$_ -gt 255 })
    })]
    [string]$IngressIp = '192.168.109.151',

    [switch]$TrustCertificate,

    [string]$CertificatePath = (Join-Path $PSScriptRoot '..\kubernetes\platform\istio\tls\lab-local-ca.crt')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Execute este script em um PowerShell aberto como Administrador.'
}

$hostsPath = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$beginMarker = '# BEGIN k8s-vmware permanent URLs'
$endMarker = '# END k8s-vmware permanent URLs'
$hostnames = @(
    'nginx.lab.local'
    'grafana.lab.local'
    'prometheus.lab.local'
    'argocd.lab.local'
    'rabbitmq.lab.local'
    'bank-moura.lab.local'
)

$current = [IO.File]::ReadAllText($hostsPath)
$blockPattern = '(?ms)^' + [regex]::Escape($beginMarker) + '\r?\n.*?^' + [regex]::Escape($endMarker) + '\r?\n?'
$withoutManagedBlock = [regex]::Replace($current, $blockPattern, '').TrimEnd("`r", "`n")
$managedBlock = @(
    $beginMarker
    "$IngressIp`t$($hostnames -join ' ')"
    $endMarker
) -join "`r`n"
$updated = $withoutManagedBlock + "`r`n`r`n" + $managedBlock + "`r`n"

if ($updated -ne $current) {
    if ($PSCmdlet.ShouldProcess($hostsPath, 'Criar backup e atualizar os nomes permanentes do laboratório')) {
        $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backupPath = "$hostsPath.k8s-vmware-$timestamp.bak"
        Copy-Item -LiteralPath $hostsPath -Destination $backupPath
        [IO.File]::WriteAllText($hostsPath, $updated, [Text.ASCIIEncoding]::new())
        & ipconfig.exe /flushdns | Out-Null
        Write-Host "Arquivo hosts atualizado. Backup: $backupPath"
    }
}
else {
    Write-Host 'Arquivo hosts já está atualizado.'
}

if ($TrustCertificate) {
    $resolvedCertificate = (Resolve-Path -LiteralPath $CertificatePath).Path
    $certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new($resolvedCertificate)
    if ($certificate.Subject -notlike '*CN=k8s-vmware Lab Root CA*') {
        throw "Certificado inesperado: $($certificate.Subject)"
    }

    if ($PSCmdlet.ShouldProcess('Cert:\LocalMachine\Root', "Confiar na CA $($certificate.Thumbprint)")) {
        $existing = Get-ChildItem Cert:\LocalMachine\Root | Where-Object Thumbprint -eq $certificate.Thumbprint
        if (-not $existing) {
            Import-Certificate -FilePath $resolvedCertificate -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
            Write-Host "CA do laboratório confiada: $($certificate.Thumbprint)"
        }
        else {
            Write-Host 'CA do laboratório já está confiada.'
        }
    }
}

Write-Host 'URLs configuradas em https://<servico>.lab.local:31882.'
