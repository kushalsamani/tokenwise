# tokenwise installer, Windows.
#
# The same five steps as install.sh, in the same order, stopping at the first failure:
#   1. refuse to run elevated
#   2. static safety audit of every hook (audit.py) -- blocking
#   3. MANIFEST.sha256 verification, so what you audited is what gets wired -- blocking unless -AllowLocalChanges
#   4. timestamped backup of ~/.claude/settings.json
#   5. idempotent merge of the hook block, rendering of the skills, creation of the local ledger
#
# It never touches your model, your context window, your permissions, or any file outside ~/.claude and this repo.
# It makes no network calls.
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -AllowLocalChanges   # your own edits, never a fresh clone
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Skip router,read_guard,agent_model_default

[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$AllowLocalChanges,
  [switch]$AllowElevated,
  [string]$Python = '',
  [string]$Skip = ''
)

$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Settings = Join-Path $env:USERPROFILE '.claude\settings.json'

# install.sh refuses root for the same reason: whatever wires these hooks decides the privileges they run with on
# every prompt from now on. -AllowElevated exists for CI, whose Windows runners are administrators by default.
$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if ($identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  if (-not $AllowElevated) {
    Write-Error 'refusing to install from an elevated shell: these hooks run with your user privileges and should never run as administrator. Re-run from a normal PowerShell window, or pass -AllowElevated if you know why you need it.'
    exit 1
  }
  Write-Host 'WARNING: installing from an elevated shell (-AllowElevated). The hooks will run elevated too.'
}

if (-not $Python) {
  $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($cmd) {
    $Python = $cmd.Source
  } else {
    $cmd = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($cmd) { $Python = (& $cmd.Source -3 -c 'import sys; print(sys.executable)') }
  }
}
if (-not $Python -or -not (Test-Path $Python)) {
  Write-Error 'Python 3.9+ is required and was not found. Install it from python.org and re-run, or pass -Python <path to python.exe>.'
  exit 1
}
# The Microsoft Store stub answers to `python` but is not an interpreter, and a hook wired to it would print
# "Python was not found" into your context on every prompt.
$probe = & $Python -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>$null
if ($LASTEXITCODE -ne 0 -or -not $probe) {
  Write-Error "the interpreter at $Python does not run. If this is the Microsoft Store alias, install real Python or pass -Python <path>."
  exit 1
}

$manifestFlag = '--manifest'
if ($AllowLocalChanges) { $manifestFlag = '' }

Write-Host '==> 1/5  safety audit'
if ($manifestFlag) { & $Python (Join-Path $Here 'audit.py') --path $Here $manifestFlag }
else { & $Python (Join-Path $Here 'audit.py') --path $Here }
if ($LASTEXITCODE -ne 0) {
  Write-Host ''
  Write-Host 'Install aborted by the audit. This is the gate that stops a merged pull request from silently wiring'
  Write-Host 'code that runs on every prompt. Read the findings above before overriding anything.'
  exit 1
}

Write-Host ''
Write-Host '==> 2/5  environment'
Write-Host "    python:   $Python  ($(& $Python --version 2>&1))"
Write-Host "    settings: $Settings"
if (-not (Test-Path $Settings)) {
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Settings) | Out-Null
  if (-not $DryRun) { Set-Content -Path $Settings -Value '{}' -Encoding utf8 }
  Write-Host '    (created)'
}

$wire = Join-Path $Here 'tools\wire.py'
# Built as an array, because PowerShell drops an empty string argument entirely: passing `--skip $Skip` with no
# -Skip given hands wire.py a bare `--skip` and argparse rejects it.
$wireArgs = @('--settings', $Settings, '--repo', $Here, '--python', $Python)
if ($Skip) { $wireArgs += @('--skip', $Skip) }

if ($DryRun) {
  Write-Host ''
  Write-Host '==> dry run: the hooks below would be wired, and nothing else would change.'
  & $Python $wire @wireArgs --dry-run
  exit 0
}

Write-Host ''
Write-Host '==> 3/5  backup'
$backup = "$Settings.bak-tokenwise-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $Settings $backup
Write-Host "    $backup"

Write-Host ''
Write-Host '==> 4/5  wiring hooks'
& $Python $wire @wireArgs
if ($LASTEXITCODE -ne 0) { Write-Error 'wiring failed; settings.json is unchanged apart from the backup above.'; exit 1 }

Write-Host ''
Write-Host '==> 5/5  skills and local state'
New-Item -ItemType Directory -Force -Path (Join-Path $env:USERPROFILE '.claude\skills') | Out-Null
& $Python (Join-Path $Here 'tools\render_skills.py') $Here $Python
$repos = Join-Path $Here 'repos.txt'
if (-not (Test-Path $repos)) { Copy-Item (Join-Path $Here 'repos.txt.example') $repos }
& $Python (Join-Path $Here 'tokenwise\ledger.py') ingest | Out-Null
Write-Host "    ledger: $(Join-Path $Here 'tokenwise\ledger.db')"

Write-Host ''
Write-Host 'Installed. Hooks take effect in NEW Claude Code sessions.'
Write-Host ''
Write-Host "  see where your tokens go          $Python $Here\tokenwise\ledger.py report --week"
Write-Host "  what a tool result really costs   $Python $Here\tokenwise\ledger.py waste --days 7"
Write-Host "  index your repos for /where-is    edit $Here\repos.txt, then $Python $Here\tokenwise\whereis.py build"
Write-Host ''
Write-Host '  turn every handler off            setx TOKENWISE_OFF 1   (new shells only)'
Write-Host "  remove completely                 powershell -ExecutionPolicy Bypass -File $Here\uninstall.ps1"
Write-Host ''
Write-Host 'Nothing about your model, context window or permissions was changed.'
