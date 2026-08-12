[CmdletBinding()]
param(
    [switch]$PromptForOpenAIKey
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$HermesHome = Join-Path $env:LOCALAPPDATA 'hermes'
$AgentRoot = Join-Path $HermesHome 'hermes-agent'
$Python = Join-Path $AgentRoot 'venv\Scripts\python.exe'
$Bootstrap = Join-Path $ProjectRoot 'runtime\bootstrap.py'
$RuntimeRoot = Join-Path $ProjectRoot 'runtime'
$Cli = Join-Path $RuntimeRoot 'goutoujunshi_cli.py'
$ConfigFile = Join-Path $HermesHome 'config.yaml'
$EnvFile = Join-Path $HermesHome '.env'
$ProfileHome = Join-Path $HermesHome 'profiles\goutoujunshi'
$RelationshipRoot = Join-Path $ProjectRoot '.local\relationships'
$LegacyFile = Get-ChildItem -LiteralPath $RelationshipRoot -Filter '*2501*.md' -File | Select-Object -First 1 -ExpandProperty FullName
$PersonName = ([string][char]0x674E) + ([string][char]0x76C8) + ([string][char]0x8431)
$ArchiveRoot = Join-Path $ProjectRoot '.local\archive\imports'
$BackupRoot = Join-Path $ProjectRoot ('.local\backups\setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$StartupFolder = [Environment]::GetFolderPath('Startup')
$OldStartup = Join-Path $StartupFolder 'Hermes_Gateway.vbs'
$SessionsFile = Join-Path $HermesHome 'sessions\sessions.json'
$CodexAuth = Join-Path $env:USERPROFILE '.codex\auth.json'
$CodexConfig = Join-Path $env:USERPROFILE '.codex\config.toml'
$WslManagerWindows = Join-Path $ProjectRoot 'scripts\wsl\Manage-Goutoujunshi-MySql.sh'

function Write-Step([string]$Message) { Write-Host "[goutoujunshi] $Message" }

function Assert-Path([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path)) { throw "$Label not found: $Path" }
}

function Import-LocalEnv {
    Get-Content -LiteralPath $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $parts = $line.Split('=',2)
            [Environment]::SetEnvironmentVariable($parts[0].Trim(),$parts[1].Trim().Trim('"').Trim("'"),'Process')
        }
    }
    $env:HERMES_HOME = $HermesHome
    $env:PYTHONPATH = $AgentRoot
}

function Protect-Directory([string]$Path) {
    $account = "$env:USERDOMAIN\$env:USERNAME"
    & icacls.exe $Path /inheritance:r /grant:r "${account}:F" /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Unable to restrict directory contents: $Path" }
    & icacls.exe $Path /grant:r "${account}:(OI)(CI)F" /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Unable to restrict directory ACL: $Path" }
}

function Protect-File([string]$Path) {
    $account = "$env:USERDOMAIN\$env:USERNAME"
    & icacls.exe $Path /inheritance:r /grant:r "${account}:F" /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Unable to restrict file ACL: $Path" }
}

function Stop-OldGateway {
    $statePath = Join-Path $HermesHome 'gateway_state.json'
    if (-not (Test-Path -LiteralPath $statePath)) { return }
    try { $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json } catch { return }
    if (-not $state.pid) { return }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($state.pid)" -ErrorAction SilentlyContinue
    if ($process -and $process.CommandLine -match 'hermes_cli\.main.+gateway.+run') {
        Stop-Process -Id $state.pid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

function Ensure-WslAnchor {
    $anchor = Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'goutoujunshi-wsl-anchor' } |
        Select-Object -First 1
    if ($anchor) { return }
    Start-Process -FilePath 'wsl.exe' -ArgumentList '-d Ubuntu -- bash -lc "exec -a goutoujunshi-wsl-anchor sleep infinity"' -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 2
}

function Ensure-MySqlReady {
    Ensure-WslAnchor
    $manager = (& wsl.exe -d Ubuntu -- wslpath -a ($WslManagerWindows.Replace('\','/'))).Trim()
    if (-not $manager) { throw 'Unable to resolve the WSL MySQL manager path' }
    & wsl.exe -d Ubuntu -- bash $manager start | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Unable to start mysql_container in Ubuntu WSL' }
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

function Wait-AppDatabase {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (Test-AppDatabase) { return }
        Start-Sleep -Seconds 2
    }
    throw 'MySQL application connection did not become healthy'
}

Assert-Path $Python 'Hermes Python'
Assert-Path $ConfigFile 'Hermes config'
Assert-Path $EnvFile 'Hermes env'
Assert-Path $SessionsFile 'Hermes session index'
Assert-Path $CodexAuth 'Codex auth'
Assert-Path $CodexConfig 'Codex config'
Assert-Path $LegacyFile 'legacy relationship file'
Assert-Path $WslManagerWindows 'WSL MySQL manager'

if ($PromptForOpenAIKey) {
    $secureKey = Read-Host 'OpenAI API Key (input is hidden)' -AsSecureString
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        $env:OPENAI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
    if (-not $env:OPENAI_API_KEY) { throw 'No OpenAI API key was entered' }
}

New-Item -ItemType Directory -Path $BackupRoot,$ArchiveRoot,(Join-Path $ProjectRoot '.local\logs'),(Join-Path $ProjectRoot '.local\backups\mysql') -Force | Out-Null
Copy-Item -LiteralPath $ConfigFile,$EnvFile -Destination $BackupRoot -Force
if (Test-Path -LiteralPath $OldStartup) { Copy-Item -LiteralPath $OldStartup -Destination $BackupRoot -Force }
Protect-Directory (Join-Path $ProjectRoot '.local\backups')
Protect-Directory $ArchiveRoot
Protect-Directory (Join-Path $ProjectRoot '.local\logs')

Write-Step 'Installing the pinned MySQL driver'
& $Python -m pip install --disable-pip-version-check --quiet 'PyMySQL==1.1.1'
if ($LASTEXITCODE -ne 0) { throw 'PyMySQL installation failed' }

Write-Step 'Preparing restricted secrets and the Feishu owner allowlist'
$StagedEnv = Join-Path (Join-Path $ProjectRoot '.local') ('staged-hermes-' + [guid]::NewGuid().ToString('N') + '.env')
try {
    Copy-Item -LiteralPath $EnvFile -Destination $StagedEnv -Force
    & $Python $Bootstrap prepare-secrets --codex-auth $CodexAuth --codex-config $CodexConfig --sessions $SessionsFile --env $StagedEnv --export-root $RelationshipRoot
    if ($LASTEXITCODE -ne 0) { throw 'Secret preparation failed' }

    Write-Step 'Preflighting GPT-5.6 Terra with high reasoning, image input, and function calling'
    & $Python $Bootstrap preflight --env $StagedEnv
    if ($LASTEXITCODE -ne 0) { throw 'OpenAI preflight failed; Hermes config was not switched' }
    Copy-Item -LiteralPath $StagedEnv -Destination $EnvFile -Force
} finally {
    if (Test-Path -LiteralPath $StagedEnv) { Remove-Item -LiteralPath $StagedEnv -Force }
}
Protect-File $EnvFile
Import-LocalEnv

Write-Step 'Starting WSL MySQL and applying the isolated schema'
Ensure-MySqlReady
$dbPassword = $env:GOUTOUJUNSHI_DB_PASSWORD
$manager = (& wsl.exe -d Ubuntu -- wslpath -a ($WslManagerWindows.Replace('\','/'))).Trim()
$schemaPath = (& wsl.exe -d Ubuntu -- wslpath -a ((Join-Path $RuntimeRoot 'goutoujunshi\schema.sql').Replace('\','/'))).Trim()
if (-not $manager -or -not $schemaPath) { throw 'Unable to resolve WSL setup paths' }
$mysqlProcessInfo = New-Object System.Diagnostics.ProcessStartInfo
$mysqlProcessInfo.FileName = 'wsl.exe'
$mysqlProcessInfo.Arguments = ('-d Ubuntu -- bash "{0}" setup "{1}"' -f $manager,$schemaPath)
$mysqlProcessInfo.UseShellExecute = $false
$mysqlProcessInfo.CreateNoWindow = $true
$mysqlProcessInfo.RedirectStandardInput = $true
$mysqlProcessInfo.RedirectStandardOutput = $true
$mysqlProcessInfo.RedirectStandardError = $true
$mysqlProcess = New-Object System.Diagnostics.Process
$mysqlProcess.StartInfo = $mysqlProcessInfo
$null = $mysqlProcess.Start()
$mysqlPasswordBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($dbPassword + "`n")
$mysqlProcess.StandardInput.BaseStream.Write($mysqlPasswordBytes,0,$mysqlPasswordBytes.Length)
$mysqlProcess.StandardInput.BaseStream.Flush()
$mysqlProcess.StandardInput.Close()
$mysqlSetupRaw = $mysqlProcess.StandardOutput.ReadToEnd()
$mysqlSetupError = $mysqlProcess.StandardError.ReadToEnd()
$mysqlProcess.WaitForExit()
if ($mysqlProcess.ExitCode -ne 0) {
    if ($mysqlSetupError) { Write-Warning $mysqlSetupError.Trim() }
    throw 'MySQL isolated schema setup failed'
}
$mysqlSetup = $mysqlSetupRaw | ConvertFrom-Json
if (-not $mysqlSetup.function_calls_unchanged) { throw 'function_calls schema changed unexpectedly' }
Wait-AppDatabase

Write-Step 'Archiving and importing the Li Yingxuan relationship file'
$legacySource = $LegacyFile
$header = Get-Content -LiteralPath $LegacyFile -TotalCount 12 -ErrorAction Stop
if (($header -join "`n") -match 'authority:\s*mysql' -and ($header -join "`n") -match 'generated:\s*true') {
    $archivedOriginal = Get-ChildItem -LiteralPath $ArchiveRoot -Filter '*2501*.md' -File |
        Sort-Object { $_.Name.Length },Name | Select-Object -First 1
    if (-not $archivedOriginal) { throw 'Generated projection exists but the immutable migration source is missing' }
    $legacySource = $archivedOriginal.FullName
}
$expectedSha = (Get-FileHash -LiteralPath $legacySource -Algorithm SHA256).Hash
$importRaw = & $Python $Cli import-legacy $legacySource --owner $env:GOUTOUJUNSHI_OWNER_ID --name $PersonName --archive-root $ArchiveRoot
if ($LASTEXITCODE -ne 0) { throw 'Legacy relationship import failed' }
$import = $importRaw | ConvertFrom-Json
if ($import.sha256 -ne $expectedSha) { throw 'Legacy import SHA256 mismatch' }

Write-Step 'Deploying the restricted Hermes plugin and profile'
& $Python $Bootstrap install-plugin --plugin-source (Join-Path $RuntimeRoot 'goutoujunshi') --target-home $HermesHome
if ($LASTEXITCODE -ne 0) { throw 'Hermes plugin deployment failed' }
& $Python $Bootstrap install-hermes-vision-patch --agent-root $AgentRoot --backup-dir (Join-Path $ProjectRoot '.local\backups\hermes-patches')
if ($LASTEXITCODE -ne 0) { throw 'Hermes bounded vision patch failed version or SHA256 verification' }
if (-not (Test-Path -LiteralPath $ProfileHome)) {
    Push-Location $AgentRoot
    try { & $Python -m hermes_cli.main profile create goutoujunshi --no-skills --no-alias --description 'Private Feishu relationship adviser backed only by the goutoujunshi MySQL database.' | Out-Null }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'Hermes profile creation failed' }
}
& $Python $Bootstrap install-skill --project-root $ProjectRoot --target-home $HermesHome
if ($LASTEXITCODE -ne 0) { throw 'Global multiplex Skill deployment failed' }
& $Python $Bootstrap install-skill --project-root $ProjectRoot --target-home $ProfileHome
if ($LASTEXITCODE -ne 0) { throw 'Relationship profile Skill deployment failed' }
& $Python $Bootstrap configure-profile --profile-home $ProfileHome --global-env $EnvFile
if ($LASTEXITCODE -ne 0) { throw 'Relationship profile configuration failed' }

Write-Step 'Setting the default GPT model with automatic fallback disabled'
& $Python $Bootstrap configure-global --config $ConfigFile --source-env $EnvFile
if ($LASTEXITCODE -ne 0) { throw 'Global Hermes configuration failed' }
& $Python $Cli reconcile-config --config $ConfigFile | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Initial route reconciliation failed' }

Write-Step 'Generating and validating the read-only Markdown projection'
& $Python $Cli retry-exports --limit 100 | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Relationship Markdown export failed' }
& $Python $Cli export $import.relationship_id | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Current relationship Markdown export failed' }
$statsRaw = & $Python $Cli stats $import.relationship_id
if ($LASTEXITCODE -ne 0) { throw 'Imported relationship validation failed' }
$stats = $statsRaw | ConvertFrom-Json
if ($stats.import.sha256 -ne $expectedSha -or $stats.events -lt $import.events) { throw 'Imported event validation mismatch' }

& $Python $Bootstrap verify --config $ConfigFile --profile-config (Join-Path $ProfileHome 'config.yaml') --env $EnvFile
if ($LASTEXITCODE -ne 0) { throw 'Hermes GPT/profile verification failed' }

Write-Step 'Replacing the duplicate Startup entry with one scheduled supervisor'
Stop-OldGateway
if (Test-Path -LiteralPath $OldStartup) {
    Move-Item -LiteralPath $OldStartup -Destination ($OldStartup + '.disabled.' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $PSScriptRoot 'Run-Goutoujunshi.ps1'))
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT30S'
$settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'Hermes-Goutoujunshi' -Action $action -Trigger $trigger -Settings $settings -User $env:USERNAME -RunLevel Limited -Force | Out-Null
Start-ScheduledTask -TaskName 'Hermes-Goutoujunshi'

Write-Step "Installed. Imported events: $($stats.events); relation id: $($import.relationship_id)."
