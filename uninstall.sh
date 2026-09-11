#!/usr/bin/env bash
# Remove tokenwise hooks and skills. The ledger and symbol index are left on disk (they are your data); delete
# tokenwise/*.db by hand if you want them gone.
set -euo pipefail
SETTINGS="$HOME/.claude/settings.json"
[ -f "$SETTINGS" ] || { echo "no settings.json; nothing to do"; exit 0; }
cp "$SETTINGS" "$SETTINGS.bak-tokenwise-uninstall-$(date +%Y%m%d-%H%M%S)"
PY=python3
command -v python3 >/dev/null || PY=python
"$PY" - "$SETTINGS" <<'PY'
import json, sys
p = sys.argv[1]; s = json.load(open(p)); hooks = s.get('hooks', {}); n = 0
for ev, groups in list(hooks.items()):
    keep = []
    for g in groups:
        if any('/tokenwise/' in (h.get('command') or '') for h in g.get('hooks', [])): n += 1
        else: keep.append(g)
    hooks[ev] = keep
s['hooks'] = {k: v for k, v in hooks.items() if v}
json.dump(s, open(p, 'w'), indent=2); open(p, 'a').write('\n')
print(f'removed {n} tokenwise hook group(s)')
PY
rm -rf "$HOME/.claude/skills/where-is" "$HOME/.claude/skills/ledger"
echo "skills removed. Your model, window and permissions were never touched."
