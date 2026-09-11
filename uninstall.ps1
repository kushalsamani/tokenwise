# Remove tokenwise hooks and skills, Windows. The ledger and symbol index are left on disk (they are your data);
# delete tokenwise\*.db by hand if you want them gone.
#
#   powershell -ExecutionPolicy Bypass -File .\uninstall.ps1

[CmdletBinding()]
param([string]$Python = '')

$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Settings = Join-Path $env:USERPROFILE '.claude\settings.json'

if (-not (Test-Path $Settings)) { Write-Host 'no settings.json; nothing to do'; exit 0 }

if (-not $Python) {
  $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($cmd) { $Python = $cmd.Source }
}
if (-not $Python) { Write-Error 'Python not found; pass -Python <path to python.exe>.'; exit 1 }

Copy-Item $Settings "$Settings.bak-tokenwise-uninstall-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

$script = @'
import json, sys
p = sys.argv[1]
s = json.load(open(p, encoding="utf-8"))
hooks = s.get("hooks", {})
n = 0
for ev, groups in list(hooks.items()):
    keep = []
    for g in groups:
        marked = any("/tokenwise/" in (h.get("command") or "") or
                     any("/tokenwise/" in x for x in (h.get("args") or []))
                     for h in g.get("hooks", []))
        if marked:
            n += 1
        else:
            keep.append(g)
    hooks[ev] = keep
s["hooks"] = {k: v for k, v in hooks.items() if v}
with open(p, "w", encoding="utf-8") as fh:
    json.dump(s, fh, indent=2)
    fh.write("\n")
print(f"removed {n} tokenwise hook group(s)")
'@

$tmp = Join-Path $env:TEMP "tokenwise-uninstall-$PID.py"
Set-Content -Path $tmp -Value $script -Encoding utf8
try { & $Python $tmp $Settings } finally { Remove-Item $tmp -ErrorAction SilentlyContinue }

foreach ($sk in 'where-is', 'ledger') {
  $p = Join-Path $env:USERPROFILE ".claude\skills\$sk"
  if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}
Write-Host 'skills removed. Your model, window and permissions were never touched.'
