# Installs the pre-commit privacy check into this clone's .git/hooks.
# Git hooks are not versioned, so run this once per clone:
#   ./scripts/install_hooks.ps1
$repo = Split-Path -Parent $PSScriptRoot
$hooksDir = Join-Path $repo ".git\hooks"
if (-not (Test-Path $hooksDir)) { throw "No .git/hooks directory found under $repo" }
$hook = Join-Path $hooksDir "pre-commit"
$script = @'
#!/bin/sh
# Installed by scripts/install_hooks.ps1: refuse commits that add private identifiers.
python "$(git rev-parse --show-toplevel)/scripts/check_staged_privacy.py"
'@
[System.IO.File]::WriteAllText($hook, ($script -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Installed $hook"
