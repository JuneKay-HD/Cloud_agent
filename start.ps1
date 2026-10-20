param(
    [ValidateRange(1024, 65535)]
    [int]$FrontendPort = 5174,
    [string]$PythonPath,
    [switch]$InstallDependencies,
    [switch]$Reload,
    [ValidateRange(10, 3600)]
    [int]$StartupTimeoutSeconds = 900,
    [ValidateSet('Backend', 'Frontend')]
    [string]$Service
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
$runtimeRoot = Join-Path $projectRoot 'tmp\runtime'
$stateFile = Join-Path $runtimeRoot 'processes.json'
$backendPort = 8000

function Start-BackendService {
    $backendRoot = Join-Path $projectRoot 'backend'
    $venvRoot = Join-Path $backendRoot '.venv'
    $python = Join-Path $venvRoot 'Scripts\python.exe'
    $requirements = Join-Path $backendRoot 'requirements.txt'
    $stamp = Join-Path $venvRoot '.requirements.sha256'
    if (-not (Test-Path -LiteralPath $python)) {
        $bootstrapArguments = @()
        if ($PythonPath) {
            $bootstrap = (Get-Command $PythonPath -ErrorAction Stop).Source
        } elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
            $bootstrap = (Get-Command py.exe).Source
            $bootstrapArguments = @('-3.12')
        } elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
            $bootstrap = (Get-Command python.exe).Source
        } else {
            throw 'Install Python 3.12 or supply -PythonPath C:\path\to\python.exe.'
        }
        & $bootstrap @bootstrapArguments -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)'
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
        & $bootstrap @bootstrapArguments -m venv $venvRoot
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create backend/.venv.' }
    }
    $requiredHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $installedHash = if (Test-Path -LiteralPath $stamp) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }
    if ($InstallDependencies -or $installedHash -ne $requiredHash) {
        Write-Host 'Installing backend dependencies...'
        & $python -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
        Set-Content -LiteralPath $stamp -Value $requiredHash -Encoding ASCII
    }
    $environmentFile = Join-Path $projectRoot '.env'
    if (-not (Test-Path -LiteralPath $environmentFile)) {
        Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination $environmentFile
        Write-Host 'Created .env from template; configure model credentials when needed.'
    }
    $serverArguments = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$backendPort")
    if ($Reload) { $serverArguments += '--reload' }
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    Push-Location -LiteralPath $backendRoot
    try {
        & $python @serverArguments
        if ($LASTEXITCODE -ne 0) { throw "Backend exited with code $LASTEXITCODE." }
    } finally { Pop-Location }
}

function Start-FrontendService {
    $frontendRoot = Join-Path $projectRoot 'frontend'
    $localNodeRoot = Join-Path $projectRoot '.tools\node-runtime\nodejs_wheel'
    $localNode = Join-Path $localNodeRoot 'node.exe'
    $localNpm = Join-Path $localNodeRoot 'lib\node_modules\npm\bin\npm-cli.js'
    $lockFile = Join-Path $frontendRoot 'package-lock.json'
    $stamp = Join-Path $frontendRoot 'node_modules\.package-lock.sha256'
    $originalPath = $env:PATH
    Push-Location -LiteralPath $frontendRoot
    try {
        if ((Test-Path -LiteralPath $localNode) -and (Test-Path -LiteralPath $localNpm)) {
            $env:PATH = "$localNodeRoot;$env:PATH"
            $npmCommand = $localNode
            $npmArguments = @($localNpm)
        } else {
            $node = Get-Command node.exe -ErrorAction SilentlyContinue
            $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
            if (-not $node -or -not $npm) { throw 'Install Node.js LTS and reopen the terminal.' }
            $npmCommand = $npm.Source
            $npmArguments = @()
        }
        $requiredHash = (Get-FileHash -LiteralPath $lockFile -Algorithm SHA256).Hash
        $installedHash = if (Test-Path -LiteralPath $stamp) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }
        $vite = Join-Path $frontendRoot 'node_modules\vite\bin\vite.js'
        if ($InstallDependencies -or -not (Test-Path -LiteralPath $vite) -or $installedHash -ne $requiredHash) {
            Write-Host 'Installing frontend dependencies...'
            & $npmCommand @npmArguments ci --cache (Join-Path $projectRoot '.tools\npm-cache')
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            Set-Content -LiteralPath $stamp -Value $requiredHash -Encoding ASCII
        }
        & $npmCommand @npmArguments run dev -- --host 127.0.0.1 --port $FrontendPort --strictPort
        if ($LASTEXITCODE -ne 0) { throw "Frontend exited with code $LASTEXITCODE." }
    } finally {
        $env:PATH = $originalPath
        Pop-Location
    }
}

function Test-HttpReady([string]$Url) {
    try { return (Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2).StatusCode -eq 200 }
    catch { return $false }
}

function Get-RecordedProcess($Record) {
    # Windows can briefly omit the executable path immediately after launch.
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        $process = Get-Process -Id ([int]$Record.Id) -ErrorAction SilentlyContinue
        if (-not $process) { return $null }
        if ($process.Path) { break }
        Start-Sleep -Milliseconds 50
    }
    if ($process.StartTime.ToUniversalTime().Ticks.ToString() -ne [string]$Record.StartTimeUtcTicks -or
        $process.Path -ine [string]$Record.ExecutablePath) {
        throw "Process identity changed for PID $($Record.Id). State retained; no process was stopped."
    }
    return $process
}

if ($Service) {
    if ($Service -eq 'Backend') { Start-BackendService } else { Start-FrontendService }
    exit 0
}

# Serialize lifecycle operations for this project directory.
$hasher = [Security.Cryptography.SHA256]::Create()
try { $projectHash = [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant()))).Replace('-', '') }
finally { $hasher.Dispose() }
$mutex = [Threading.Mutex]::new($false, "Local\CloudAgent-$($projectHash.Substring(0, 24))")
$locked = $false
$launched = @()
$runId = [guid]::NewGuid().ToString()
try {
    try { $locked = $mutex.WaitOne(10000) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Another start/stop operation is in progress. Retry after it finishes.' }
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    if (Test-Path -LiteralPath $stateFile) {
        $existing = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($existing.ProjectRoot -ine $projectRoot) { throw 'Runtime state belongs to another project directory.' }
        $running = @($existing.Processes | ForEach-Object { Get-RecordedProcess $_ } | Where-Object { $_ })
        if ($running.Count -gt 0) {
            if ($running.Count -eq 2 -and
                (Test-HttpReady 'http://127.0.0.1:8000/api/health') -and
                (Test-HttpReady "http://127.0.0.1:$($existing.FrontendPort)/api/health")) {
                Write-Host "Cloud_Agent is already running: http://127.0.0.1:$($existing.FrontendPort)"
                return
            }
            throw 'A recorded service is still running but not ready. Run stop.ps1 before restarting.'
        }
        Remove-Item -LiteralPath $stateFile
    }
    if ($FrontendPort -eq $backendPort) { throw 'FrontendPort must differ from backend port 8000.' }
    foreach ($port in @($backendPort, $FrontendPort)) {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $port)
        $listener.Server.ExclusiveAddressUse = $true
        try { $listener.Start() }
        catch {
            if ($port -eq $backendPort) {
                throw "Port $port is occupied. Stop the owning backend in its original terminal, or run stop.ps1 from the project that started it. -FrontendPort does not change the backend port. No existing service was stopped."
            }
            throw "Frontend port $port is occupied. Stop the owning frontend or choose another -FrontendPort. No existing service was stopped."
        }
        finally { $listener.Stop() }
    }
    $shell = (Get-Command powershell.exe -ErrorAction Stop).Source
    if ($PythonPath) { $PythonPath = (Get-Command $PythonPath -ErrorAction Stop).Source }
    $logTag = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    foreach ($role in @('Backend', 'Frontend')) {
        $stdout = Join-Path $runtimeRoot "$logTag-$($role.ToLowerInvariant()).stdout.log"
        $stderr = Join-Path $runtimeRoot "$logTag-$($role.ToLowerInvariant()).stderr.log"
        $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-Service', $role, '-FrontendPort', "$FrontendPort")
        if ($InstallDependencies) { $arguments += '-InstallDependencies' }
        if ($Reload) { $arguments += '-Reload' }
        if ($PythonPath) { $arguments += @('-PythonPath', ('"' + $PythonPath + '"')) }
        $process = Start-Process -FilePath $shell -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        $launched += [pscustomobject]@{
            Role = $role
            Id = $process.Id
            StartTimeUtcTicks = $process.StartTime.ToUniversalTime().Ticks.ToString()
            ExecutablePath = $shell
            Stdout = $stdout
            Stderr = $stderr
        }
        [ordered]@{ Version = 1; ProjectRoot = $projectRoot; RunId = $runId; FrontendPort = $FrontendPort; BackendPort = $backendPort; Processes = @($launched) } |
            ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateFile -Encoding UTF8
    }
    Write-Host "Starting Cloud_Agent. Logs: $runtimeRoot"
    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    do {
        foreach ($record in $launched) {
            if (-not (Get-RecordedProcess $record)) { throw "$($record.Role) exited during startup. Check $($record.Stderr)" }
        }
        if ((Test-HttpReady 'http://127.0.0.1:8000/api/health') -and
            (Test-HttpReady "http://127.0.0.1:$FrontendPort/") -and
            (Test-HttpReady "http://127.0.0.1:$FrontendPort/api/health")) {
            Write-Host "Frontend: http://127.0.0.1:$FrontendPort"
            Write-Host 'Backend:  http://127.0.0.1:8000'
            Write-Host 'Both services are ready. Run stop.ps1 to stop them.'
            return
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Startup timed out after $StartupTimeoutSeconds seconds. Check logs in $runtimeRoot."
} catch {
    $startupError = $_
    # Roll back only processes created by this invocation.
    foreach ($record in $launched) {
        try {
            $process = Get-RecordedProcess $record
            if ($process) {
                & taskkill.exe /PID "$($process.Id)" /T /F | Out-Host
                if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
                    throw "Failed to stop PID $($process.Id)."
                }
            }
        } catch { Write-Warning "Rollback incomplete: $($_.Exception.Message). Run stop.ps1." }
    }
    if ($launched.Count -gt 0 -and (Test-Path -LiteralPath $stateFile)) {
        $remaining = @($launched | Where-Object { Get-Process -Id ([int]$_.Id) -ErrorAction SilentlyContinue })
        if ($remaining.Count -eq 0) {
            $saved = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($saved.RunId -eq $runId) { Remove-Item -LiteralPath $stateFile }
        }
    }
    throw $startupError
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
