[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Start', 'Stop')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$TaskName = 'Hermes-Goutoujunshi'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$HermesHome = Join-Path $env:LOCALAPPDATA 'hermes'
$AgentRoot = Join-Path $HermesHome 'hermes-agent'
$Python = Join-Path $AgentRoot 'venv\Scripts\python.exe'
$GatewayStateFile = Join-Path $HermesHome 'gateway_state.json'
$WslManagerWindows = Join-Path $ProjectRoot 'scripts\wsl\Manage-Goutoujunshi-MySql.sh'

function Get-GatewayState {
    if (-not (Test-Path -LiteralPath $GatewayStateFile)) { return $null }
    try { return Get-Content -LiteralPath $GatewayStateFile -Raw | ConvertFrom-Json } catch { return $null }
}

function Test-GatewayConnected {
    $state = Get-GatewayState
    if (-not $state -or -not $state.pid) { return $false }
    $process = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    return ($null -ne $process -and
        $state.gateway_state -eq 'running' -and
        $state.platforms.feishu.state -eq 'connected')
}

function Stop-GatewayGracefully {
    $state = Get-GatewayState
    if (-not $state -or -not $state.pid) { return }
    if (-not (Get-Process -Id $state.pid -ErrorAction SilentlyContinue)) { return }

    if (-not (Test-Path -LiteralPath $Python)) { throw "Hermes Python not found: $Python" }
    $env:HERMES_HOME = $HermesHome
    $env:PYTHONPATH = $AgentRoot
    Push-Location $AgentRoot
    try {
        & $Python -c 'from hermes_cli.gateway import stop_profile_gateway; stop_profile_gateway()' 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Hermes Gateway graceful stop failed' }
    } finally {
        Pop-Location
    }

    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (-not (Get-Process -Id $state.pid -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "Hermes Gateway process $($state.pid) did not stop; no force-stop was attempted"
}

function Stop-MySqlGracefully {
    $manager = (& wsl.exe -d Ubuntu -- wslpath -a ($WslManagerWindows.Replace('\','/'))).Trim()
    if (-not $manager) { throw 'WSL MySQL manager path resolution failed' }
    & wsl.exe -d Ubuntu -- bash $manager stop 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'WSL MySQL graceful stop failed' }
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop

if ($Action -eq 'Start') {
    if (-not $PSCmdlet.ShouldProcess($TaskName, 'Start all Hermes relationship services')) { return }
    if ($task.State -ne 'Running') {
        Start-ScheduledTask -TaskName $TaskName
    }
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if (Test-GatewayConnected) {
            Write-Host 'Goutoujunshi is running: MySQL healthy, Hermes Gateway healthy, Feishu connected.'
            return
        }
        Start-Sleep -Seconds 2
    }
    throw 'Startup timed out before the Feishu connection became healthy; check .local\logs\supervisor.log'
}

if (-not $PSCmdlet.ShouldProcess($TaskName, 'Stop all Hermes relationship services and terminate Ubuntu WSL')) { return }
if ($task.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $TaskName
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ((Get-ScheduledTask -TaskName $TaskName).State -ne 'Running') { break }
        Start-Sleep -Milliseconds 250
    }
}

Stop-GatewayGracefully
Stop-MySqlGracefully
& wsl.exe --terminate Ubuntu 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Ubuntu WSL termination failed' }
Write-Host 'Goutoujunshi stopped: supervisor, Hermes Gateway, MySQL, and Ubuntu WSL are stopped.'
