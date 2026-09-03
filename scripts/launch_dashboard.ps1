param(
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$dashboardUrl = "http://localhost:3402/"
$statusUrl = "http://127.0.0.1:3402/api/status"
$repoRoot = Split-Path -Parent $PSScriptRoot
$serverScript = Join-Path $PSScriptRoot "serve.py"
$logDirectory = Join-Path $repoRoot "tmp"
$stdoutLog = Join-Path $logDirectory "finance-server.stdout.log"
$stderrLog = Join-Path $logDirectory "finance-server.stderr.log"

function Test-FinanceServer {
    try {
        $status = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 2
        return $status.ok -eq $true -and $status.editable -eq $true -and `
            $status.autoStop -eq $true
    } catch {
        return $false
    }
}

function Get-FinanceServerProcesses {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine.IndexOf($serverScript, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

function Test-PortInUse {
    return $null -ne (Get-NetTCPConnection -LocalPort 3402 -State Listen `
        -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Show-LaunchError([string]$message) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        $message,
        "Finances could not start",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}

try {
    $serverProcesses = @(Get-FinanceServerProcesses)
    $serverReady = Test-FinanceServer

    # Recover from an interrupted launch or an older launcher that left more
    # than one copy running. Multiple listeners can make requests fail at random.
    if (-not $serverReady -or $serverProcesses.Count -gt 1) {
        foreach ($serverProcess in $serverProcesses) {
            Stop-Process -Id $serverProcess.ProcessId -Force -ErrorAction SilentlyContinue
        }

        $portDeadline = (Get-Date).AddSeconds(4)
        while ((Get-Date) -lt $portDeadline -and (Test-PortInUse)) {
            Start-Sleep -Milliseconds 100
        }
        if (Test-PortInUse) {
            throw "Port 3402 is being used by another application. Close it, then try again."
        }

        $python = Get-Command python.exe -ErrorAction Stop
        New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

        Start-Process `
            -FilePath $python.Source `
            -ArgumentList @("`"$serverScript`"", "--auto-stop") `
            -WorkingDirectory $repoRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog

        $deadline = (Get-Date).AddSeconds(12)
        while ((Get-Date) -lt $deadline -and -not (Test-FinanceServer)) {
            Start-Sleep -Milliseconds 250
        }

        if (-not (Test-FinanceServer)) {
            throw "The local finance server started but did not respond."
        }
    }

    if (-not $NoOpen) {
        Start-Process $dashboardUrl
    }
} catch {
    if ($NoOpen) {
        Write-Error $_.Exception.Message
    } else {
        Show-LaunchError $_.Exception.Message
    }
    exit 1
}
