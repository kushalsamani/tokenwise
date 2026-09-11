#!/usr/bin/env bash
# tokenwise installer.
#
# Runs in this order, and stops at the first failure:
#   1. refuse to run as root
#   2. static safety audit of every hook (audit.py) -- blocking
#   3. MANIFEST.sha256 verification, so what you audited is what gets wired -- blocking unless --allow-local-changes
#   4. timestamped backup of ~/.claude/settings.json
#   5. idempotent merge of the hook block, symlinking of skills, creation of the local ledger
#
# It never touches your model, your context window, your permissions, or any file outside ~/.claude and this repo.
# It makes no network calls. It is non-interactive by design: there is nothing to choose.
#
#   ./install.sh                      audit, verify, install
#   ./install.sh --dry-run            show exactly what would change, touch nothing
#   ./install.sh --allow-local-changes    skip manifest verification (for your own edits; never for a fresh clone)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"
DRY=0
MANIFEST_CHECK=--manifest

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --allow-local-changes) MANIFEST_CHECK="" ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" = "0" ]; then
  echo "refusing to install as root: these hooks run with your user's privileges and should never run as root." >&2
  exit 1
fi

PY=python3
command -v python3 >/dev/null || PY=python
command -v "$PY" >/dev/null || { echo "python3 is required (stdlib only, no packages)." >&2; exit 1; }

echo "==> 1/5  safety audit"
"$PY" "$HERE/audit.py" --path "$HERE" $MANIFEST_CHECK || {
  echo
  echo "Install aborted by the audit. This is the gate that stops a merged pull request from silently wiring"
  echo "code that runs on every prompt. Read the findings above before overriding anything."
  exit 1
}

echo
echo "==> 2/5  environment"
echo "    python:   $("$PY" --version 2>&1)"
echo "    settings: $SETTINGS"
[ -f "$SETTINGS" ] || { mkdir -p "$(dirname "$SETTINGS")"; [ "$DRY" = 1 ] || echo '{}' > "$SETTINGS"; echo "    (created)"; }

if [ "$DRY" = 1 ]; then
  echo
  echo "==> dry run: the hooks below would be wired, and nothing else would change."
  "$PY" "$HERE/tools/wire.py" --settings "$SETTINGS" --repo "$HERE" --dry-run
  exit 0
fi

echo
echo "==> 3/5  backup"
BACKUP="$SETTINGS.bak-tokenwise-$(date +%Y%m%d-%H%M%S)"
cp "$SETTINGS" "$BACKUP"
echo "    $BACKUP"

echo
echo "==> 4/5  wiring hooks"
"$PY" "$HERE/tools/wire.py" --settings "$SETTINGS" --repo "$HERE"

echo
echo "==> 5/5  skills and local state"
mkdir -p "$HOME/.claude/skills"
"$PY" "$HERE/tools/render_skills.py" "$HERE" "$PY"
chmod +x "$HERE"/tokenwise/hooks/*.py "$HERE"/tokenwise/*.py "$HERE"/harness/run.sh "$HERE"/audit.py 2>/dev/null || true
[ -f "$HERE/repos.txt" ] || cp "$HERE/repos.txt.example" "$HERE/repos.txt"
"$PY" "$HERE/tokenwise/ledger.py" ingest >/dev/null 2>&1 || true
echo "    ledger: $HERE/tokenwise/ledger.db"

cat <<EOF

Installed. Hooks take effect in NEW Claude Code sessions.

  see where your tokens go     $PY $HERE/tokenwise/ledger.py report --week
  what a tool result really costs   $PY $HERE/tokenwise/ledger.py waste --days 7
  index your repos for /where-is    edit $HERE/repos.txt, then $PY $HERE/tokenwise/whereis.py build

  turn every handler off        export TOKENWISE_OFF=1
  remove completely             $HERE/uninstall.sh   (restores from $BACKUP)

Nothing about your model, context window or permissions was changed.
EOF
