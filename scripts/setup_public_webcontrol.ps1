param(
    [string]$Domain = "sm.e-bold.dk",
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"

Write-Host "Server Manager - Public Web Control setup" -ForegroundColor Cyan
Write-Host ""

$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Warning "Kør scriptet som Administrator for at oprette firewall-regel automatisk."
}

$ruleName = "ServerManager-WebControl-$Port"

if ($admin) {
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $existing) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
        Write-Host "Firewall-regel oprettet: $ruleName" -ForegroundColor Green
    }
    else {
        Write-Host "Firewall-regel findes allerede: $ruleName" -ForegroundColor Yellow
    }
}

$localIp = (
    Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "169.254*" -and $_.IPAddress -ne "127.0.0.1" } |
    Sort-Object InterfaceMetric |
    Select-Object -First 1 -ExpandProperty IPAddress
)

if (-not $localIp) {
    $localIp = "<din-lokale-ip>"
}

Write-Host ""
Write-Host "Næste trin (manuelt):" -ForegroundColor Cyan
Write-Host "1) Router port forward: TCP $Port -> $localIp`:$Port"
Write-Host "2) DNS A-record: $Domain -> <din-offentlige-ip>"
Write-Host ""
Write-Host "Når Web Control er aktiveret i appen kan du bruge:" -ForegroundColor Cyan
Write-Host "http://$Domain`:$Port"
Write-Host ""

if ($admin -and $localIp -ne "<din-lokale-ip>") {
    $listen = Test-NetConnection -ComputerName $localIp -Port $Port -WarningAction SilentlyContinue
    if ($listen.TcpTestSucceeded) {
        Write-Host "Lokal porttest OK: $localIp`:$Port svarer." -ForegroundColor Green
    }
    else {
        Write-Warning "Porten svarer ikke lokalt endnu. Aktivér Web Control i appen først."
    }
}
