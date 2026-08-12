[CmdletBinding()]
param(
    [int]$IntervalSeconds = 60
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$HermesHome = Join-Path $env:LOCALAPPDATA 'hermes'
$AgentRoot = Join-Path $HermesHome 'hermes-agent'
$Python = Join-Path $AgentRoot 'venv\Scripts\python.exe'
$EnvFile = Join-Path $HermesHome '.env'
$ConfigFile = Join-Path $HermesHome 'config.yaml'
$RuntimeRoot = Join-Path $ProjectRoot 'runtime'
$Cli = Join-Path $RuntimeRoot 'goutoujunshi_cli.py'
$LogRoot = Join-Path $ProjectRoot '.local\logs'
$BackupRoot = Join-Path $ProjectRoot '.local\backups\mysql'
$LogFile = Join-Path $LogRoot 'supervisor.log'
$WslManagerWindows = Join-Path $ProjectRoot 'scripts\wsl\Manage-Goutoujunshi-MySql.sh'
$script:FeishuFailures = 0

New-Item -ItemType Directory -Path $LogRoot,$BackupRoot -Force | Out-Null

function Write-StateLog([string]$Component, [string]$Code, [string]$Message) {
    $clean = ($Message -replace '[\r\n]+',' ') -replace '(?i)(key|secret|password|token)=[^ ]+','$1=REDACTED'
    $line = '{0}`t{1}`t{2}`t{3}' -f (Get-Date).ToString('o'),$Component,$Code,$clean
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

function Import-LocalEnv {
    Get-Content -LiteralPath $EnvFile -ErrorAction Stop | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $parts = $line.Split('=',2)
            [Environment]::SetEnvironmentVariable($parts[0].Trim(),$parts[1].Trim().Trim('"').Trim("'"),'Process')
        }
    }
    $env:HERMES_HOME = $HermesHome
    $env:PYTHONPATH = $AgentRoot
}

function Ensure-WslAnchor {
    $anchor = Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'goutoujunshi-wsl-anchor' } |
        Select-Object -First 1
    if ($anchor) { return }
    Start-Process -FilePath 'wsl.exe' -ArgumentList '-d Ubuntu -- bash -lc "exec -a goutoujunshi-wsl-anchor sleep infinity"' -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 2
    Write-StateLog 'wsl' 'anchor_start' 'WSL keepalive process started'
}

function Test-AppDatabase {
    $healthInfo = New-Object System.Diagnostics.ProcessStartInfo
    $healthInfo.FileName = $Python
    $healthInfo.Arguments = ('"{0}" health' -f $Cli)
    $healthInfo.WorkingDirectory = $ProjectRoot
    $healthInfo.UseShellExecute = $false
    $healthInfo.CreateNoWindow = $true
    $healthInfo.RedirectStandardOutput = $true
    $healthInfo.RedirectStandardError = $true
    $healthProcess = New-Object System.Diagnostics.Process
    $healthProcess.StartInfo = $healthInfo
    $null = $healthProcess.Start()
    $null = $healthProcess.StandardOutput.ReadToEnd()
    $null = $healthProcess.StandardError.ReadToEnd()
    $healthProcess.WaitForExit()
    return $healthProcess.ExitCode -eq 0
}

function Ensure-MySql {
    Ensure-WslAnchor
    $manager = (& wsl.exe -d Ubuntu -- wslpath -a ($WslManagerWindows.Replace('\','/'))).Trim()
    if (-not $manager) { throw 'WSL MySQL manager path resolution failed' }
    & wsl.exe -d Ubuntu -- bash $manager start 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'WSL MySQL start failed' }
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        if (Test-AppDatabase) { return }
        Start-Sleep -Seconds 2
    }
    throw 'MySQL did not become healthy'
}

function Get-GatewayState {
    $path = Join-Path $HermesHome 'gateway_state.json'
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try { return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json } catch { return $null }
}

function Stop-ManagedGateway {
    $state = Get-GatewayState
    if (-not $state -or -not $state.pid) { return }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($state.pid)" -ErrorAction SilentlyContinue
    if ($process -and $process.CommandLine -match 'hermes_cli\.main.+gateway.+run') {
        & $Python -c 'from hermes_cli.gateway import stop_profile_gateway; raise SystemExit(0 if stop_profile_gateway() else 2)' 2>$null | Out-Null
        $remaining = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
        if ($remaining) {
            Write-StateLog 'gateway' 'graceful_stop_failed' 'Gateway did not exit after planned stop; forcing recovery'
            Stop-Process -Id $state.pid -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
    }
}

function Start-ManagedGateway {
    $state = Get-GatewayState
    $alive = $false
    if ($state -and $state.pid) { $alive = $null -ne (Get-Process -Id $state.pid -ErrorAction SilentlyContinue) }
    if ($alive -and $state.gateway_state -eq 'running') { return }
    Start-Process -FilePath $Python -ArgumentList @('-m','hermes_cli.main','gateway','run') -WorkingDirectory $AgentRoot -WindowStyle Hidden | Out-Null
    Write-StateLog 'gateway' 'start' 'Gateway process started'
}

function Reconcile-Routes {
    $raw = & $Python $Cli reconcile-config --config $ConfigFile 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'route reconciliation failed' }
    $result = $raw | ConvertFrom-Json
    if ($result.changed) {
        Stop-ManagedGateway
        Start-ManagedGateway
        Write-StateLog 'gateway' 'routes_changed' "Active relationship routes: $($result.active_routes)"
    }
}

function Check-Feishu {
    $state = Get-GatewayState
    $connected = $state -and $state.gateway_state -eq 'running' -and $state.platforms.feishu.state -eq 'connected'
    if ($connected) { $script:FeishuFailures = 0; return }
    $script:FeishuFailures++
    if ($script:FeishuFailures -ge 3) {
        Stop-ManagedGateway
        Start-ManagedGateway
        $script:FeishuFailures = 0
        Write-StateLog 'feishu' 'reconnect' 'Connection unhealthy for three checks; Gateway restarted'
    }
}

function Retry-Exports {
    $raw = & $Python $Cli retry-exports --limit 25 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'export retry failed' }
    $result = $raw | ConvertFrom-Json
    if ($result.done -or $result.failed) { Write-StateLog 'export' 'retry' "done=$($result.done) failed=$($result.failed)" }
}

function Clear-ExpiredRelationshipMedia {
    $registryPath = Join-Path $HermesHome 'state\goutoujunshi-media.json'
    if (-not (Test-Path -LiteralPath $registryPath)) { return }
    try { $entries = @(Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json) } catch { return }
    $allowedRoots = @('images','audio','documents') | ForEach-Object { [IO.Path]::GetFullPath((Join-Path $HermesHome "cache\$_")) + [IO.Path]::DirectorySeparatorChar }
    $cutoff = [DateTimeOffset]::UtcNow.AddHours(-24)
    $retained = @()
    foreach ($entry in $entries) {
        if (-not $entry -or -not $entry.path -or -not $entry.created_at) { continue }
        $created = [DateTimeOffset]::MinValue
        $validDate = [DateTimeOffset]::TryParse([string]$entry.created_at,[ref]$created)
        try { $fullPath = [IO.Path]::GetFullPath([string]$entry.path) } catch { continue }
        $allowed = @($allowedRoots | Where-Object { $fullPath.StartsWith($_,[StringComparison]::OrdinalIgnoreCase) }).Count -gt 0
        if ($validDate -and $created -lt $cutoff) {
            if ($allowed) {
                $file = Get-Item -LiteralPath $fullPath -ErrorAction SilentlyContinue
                if ($file -and -not $file.PSIsContainer) { $file.Delete() }
            }
            continue
        }
        if ($validDate) { $retained += $entry }
    }
    $temporary = "$registryPath.tmp"
    $json = ConvertTo-Json -InputObject @($retained) -Depth 3
    [IO.File]::WriteAllText($temporary,$json,(New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $registryPath -Force
}

function Backup-MySqlDaily {
    $today = Get-Date -Format 'yyyy-MM-dd'
    $target = Join-Path $BackupRoot "goutoujunshi-$today.sql"
    if (Test-Path -LiteralPath $target) { return }
    $wslTarget = (& wsl.exe -d Ubuntu -- wslpath -a ($target.Replace('\','/'))).Trim()
    $manager = (& wsl.exe -d Ubuntu -- wslpath -a ($WslManagerWindows.Replace('\','/'))).Trim()
    if (-not $wslTarget -or -not $manager) { throw 'unable to resolve WSL backup paths' }
    & wsl.exe -d Ubuntu -- bash $manager backup $wslTarget 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'database dump failed' }
    if (-not (Test-Path -LiteralPath $target)) { throw 'database backup copy failed' }
    Get-ChildItem -LiteralPath $BackupRoot -Filter 'goutoujunshi-*.sql' -File |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
        Remove-Item -Force
    Write-StateLog 'backup' 'done' "Created $([IO.Path]::GetFileName($target))"
}

$createdNew = $false
$mutex = New-Object Threading.Mutex($true,'Local\Hermes-Goutoujunshi-Supervisor',[ref]$createdNew)
if (-not $createdNew) { exit 0 }

try {
    Import-LocalEnv
    Write-StateLog 'supervisor' 'start' 'Supervisor started'
    while ($true) {
        try {
            Ensure-MySql
            Reconcile-Routes
            Start-ManagedGateway
            Check-Feishu
            Retry-Exports
            Clear-ExpiredRelationshipMedia
            Backup-MySqlDaily
        } catch {
            Write-StateLog 'supervisor' 'error' $_.Exception.GetType().Name
        }
        Start-Sleep -Seconds ([Math]::Max(15,$IntervalSeconds))
    }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
