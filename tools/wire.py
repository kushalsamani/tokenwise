#!/usr/bin/env python3
"""wire.py — the only thing in this repo allowed to touch ~/.claude/settings.json.

Kept separate from the hooks on purpose: the audit forbids hooks from mentioning settings.json at all, so the file
that edits your configuration is a single, short, reviewable script that never runs automatically.

The merge is idempotent: every previous tokenwise entry is removed before the current set is added, so re-running
after a `git pull` updates the wiring instead of duplicating it. Hooks belonging to other tools are left alone.
"""
import argparse
import json
import os
import sys

# event -> (matcher or None, hook filename, timeout seconds)
HOOKS = [
    ('UserPromptSubmit', None, 'router.py', 10),
    ('UserPromptSubmit', None, 'task_boundary.py', 20),
    ('PreToolUse', 'Read', 'read_guard.py', 10),
    ('PreToolUse', 'Agent', 'agent_model_default.py', 10),
    ('PostToolUse', '*', 'context_governor.py', 10),
    ('Stop', None, 'context_governor.py', 10),
]
MARKER = '/tokenwise/'          # how we recognise our own entries on the way back out


def build(repo):
    out = {}
    for event, matcher, name, timeout in HOOKS:
        path = os.path.join(repo, 'tokenwise', 'hooks', name)
        if not os.path.exists(path):
            print(f'  MISSING {path}', file=sys.stderr)
            sys.exit(1)
        entry = {'hooks': [{'type': 'command', 'command': f'/usr/bin/env python3 "{path}"', 'timeout': timeout}]}
        if matcher is not None:
            entry['matcher'] = matcher
        out.setdefault(event, []).append(entry)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--settings', required=True)
    ap.add_argument('--repo', required=True)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    settings = json.load(open(a.settings)) if os.path.exists(a.settings) else {}
    hooks = settings.get('hooks', {})
    wanted = build(os.path.abspath(a.repo))

    removed = 0
    for event, groups in list(hooks.items()):
        keep = []
        for g in groups:
            if any(MARKER in (h.get('command') or '') for h in g.get('hooks', [])):
                removed += 1
            else:
                keep.append(g)
        hooks[event] = keep
    for event, groups in wanted.items():
        hooks.setdefault(event, []).extend(groups)
    hooks = {k: v for k, v in hooks.items() if v}

    for event, matcher, name, timeout in HOOKS:
        print(f'    {event:<17} {matcher or "*":<7} {name}  ({timeout}s)')
    if removed:
        print(f'    (replaced {removed} previous tokenwise entr{"y" if removed == 1 else "ies"})')

    untouched = sum(len(g) for e, gs in hooks.items() for g in [gs]
                    for _ in [0]) - sum(len(v) for v in wanted.values())
    print(f'    other tools\' hooks left untouched: {max(untouched, 0)} group(s)')

    if a.dry_run:
        print('    dry run: settings.json not written')
        return
    settings['hooks'] = hooks
    with open(a.settings, 'w') as fh:
        json.dump(settings, fh, indent=2)
        fh.write('\n')
    json.load(open(a.settings))          # fail loudly rather than leave a corrupt settings file
    print('    settings.json updated and re-parsed OK')


if __name__ == '__main__':
    main()
