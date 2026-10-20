$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
$stateFile = Join-Path $projectRoot 'tmp\runtime\processes.json'
$hasher = [Security.Cryptography.SHA256]::Create()
try { $projectHash = [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant()))).Replace('-', '') }
finally { $hasher.Dispose() }
$mutex = [Threading.Mutex]::new($false, "Local\CloudAgent-$($projectHash.Substring(0, 24))")
$locked = $false
try {
    try { $locked = $mutex.WaitOne(10000) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Another start/stop operation is in progress. Retry after it finishes.' }
    if (-not (Test-Path -LiteralPath $stateFile)) {
        Write-Host 'No managed Cloud_Agent services are running.'
        return
    }
    $state = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($state.ProjectRoot -ine $projectRoot -or $state.Version -ne 1) {
        throw 'Runtime state does not match this project. No process was stopped.'
    }
    # Validate every identity before stopping any process, including PID reuse.
    $processes = @()
    foreach ($record in @($state.Processes)) {
        if ($record.Role -notin @('Backend', 'Frontend') -or [int]$record.Id -le 0 -or [int]$record.Id -eq $PID) {
            throw 'Invalid process record. No process was stopped.'
        }
        $process = Get-Process -Id ([int]$record.Id) -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        if ($process.StartTime.ToUniversalTime().Ticks.ToString() -ne [string]$record.StartTimeUtcTicks -or
            $process.Path -ine [string]$record.ExecutablePath) {
            throw "Process identity changed for PID $($record.Id). State retained; no process was stopped."
        }
        $processes += $process
    }
    foreach ($process in $processes) {
        Write-Host "Stopping Cloud_Agent process tree: $($process.Id)"
        & taskkill.exe /PID "$($process.Id)" /T /F | Out-Host
        if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
            throw "Failed to stop PID $($process.Id). State retained for retry."
        }
    }
    foreach ($process in $processes) {
        if (-not $process.WaitForExit(5000)) { throw "PID $($process.Id) has not exited. State retained for retry." }
    }
    Remove-Item -LiteralPath $stateFile
    Write-Host 'Cloud_Agent stopped. Logs, configuration, and data were retained.'
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
