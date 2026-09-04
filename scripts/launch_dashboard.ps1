param(
    [switch]$NoOpen,
    [ValidateRange(1, 65535)]
    [int]$Port = 3403
)

$ErrorActionPreference = "Stop"
$dashboardUrl = "http://localhost:$Port/"
$statusUrl = "http://127.0.0.1:$Port/api/status"
$repoRoot = Split-Path -Parent $PSScriptRoot
$serverScript = Join-Path $PSScriptRoot "serve.py"
$logDirectory = Join-Path $repoRoot "tmp"
$stdoutLog = Join-Path $logDirectory "finance-server.stdout.log"
$stderrLog = Join-Path $logDirectory "finance-server.stderr.log"

function Test-FinanceServer {
    # A healthy server is one that answers and can still save edits. autoStop is
    # deliberately NOT required: a server started by hand with
    # "python scripts/serve.py" runs without it and must be reused, not replaced,
    # so it keeps running until its own Ctrl+C.
    try {
        $status = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 2
        return $status.ok -eq $true -and $status.editable -eq $true
    } catch {
        return $false
    }
}

function Get-FinanceServerProcesses {
    # Enumerating every process on the machine just to read CommandLine is slow;
    # ask the CIM provider for the python hosts only.
    @(Get-CimInstance Win32_Process `
        -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" | Where-Object {
        $_.CommandLine -and
        $_.CommandLine.IndexOf($serverScript, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

function Test-PortInUse {
    return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen `
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

    # One healthy server - however it was started - is left alone; just open the
    # dashboard against it.
    #
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
            throw "Port $Port is being used by another application. Close it, then try again."
        }

        $python = Get-Command python.exe -ErrorAction Stop
        # Start-Process truncates both redirect targets on every launch, which is
        # what we want (one launch, one log); it only needs tmp/ to exist first.
        New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

        Start-Process `
            -FilePath $python.Source `
            -ArgumentList @("`"$serverScript`"", "--auto-stop", "--port", "$Port") `
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
