#!/usr/bin/env python3
"""wire.py — the only thing in this repo allowed to touch ~/.claude/settings.json.

Kept separate from the hooks on purpose: the audit forbids hooks from mentioning settings.json at all, so the file
that edits your configuration is a single, short, reviewable script that never runs automatically.

The merge is idempotent: every previous tokenwise entry is removed before the current set is added, so re-running
after a `git pull` updates the wiring instead of duplicating it. Hooks belonging to other tools are left alone.

HOW THE COMMAND IS BUILT, AND WHY IT DIFFERS BY PLATFORM
    On macOS and Linux a hook command is a shell string, and `/usr/bin/env python3 "<path>"` is the right one.
    On Windows neither half of that holds. There is no `/usr/bin/env`; `python3` is usually a Microsoft Store
    stub that prints "Python was not found" and exits 0, which Claude Code would then inject into your context on
    every prompt; and the command may be handed to PowerShell, where a quoted path at the start of a line is a
    string literal rather than a program to run. So on Windows the hook is wired in exec form instead:

        {"type": "command", "command": "<python.exe>", "args": ["<hook.py>"]}

    which bypasses the shell entirely, and `--form shell` is there if you ever need the old shape back.
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
WINDOWS = os.name == 'nt'


def _posix(path):
    """Forward slashes everywhere: Windows accepts them, and they need no escaping in JSON or in a shell."""
    return path.replace('\\', '/')


def skipped(spec, event, name):
    """--skip takes `router` (every event) or `context_governor:PostToolUse` (one event)."""
    stem = name[:-3] if name.endswith('.py') else name
    for entry in spec:
        want, _, ev = entry.partition(':')
        if want.replace('.py', '') == stem and (not ev or ev.lower() == event.lower()):
            return True
    return False


def build(repo, python, form, skip):
    out = {}
    for event, matcher, name, timeout in HOOKS:
        path = os.path.join(repo, 'tokenwise', 'hooks', name)
        if not os.path.exists(path):
            print(f'  MISSING {path}', file=sys.stderr)
            sys.exit(1)
        if skipped(skip, event, name):
            continue
        if form == 'exec':
            hook = {'type': 'command', 'command': _posix(python), 'args': [_posix(path)], 'timeout': timeout}
        else:
            launcher = f'"{_posix(python)}"' if WINDOWS else '/usr/bin/env python3'
            hook = {'type': 'command', 'command': f'{launcher} "{_posix(path)}"', 'timeout': timeout}
        entry = {'hooks': [hook]}
        if matcher is not None:
            entry['matcher'] = matcher
        out.setdefault(event, []).append(entry)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--settings', required=True)
    ap.add_argument('--repo', required=True)
    ap.add_argument('--python', default=None,
                    help='interpreter to run the hooks with (default: the one running this script, on Windows)')
    ap.add_argument('--form', choices=['exec', 'shell'], default=None,
                    help='exec passes argv directly and skips the shell; shell writes one command string')
    ap.add_argument('--skip', default='', help='comma-separated: `router` or `context_governor:PostToolUse`')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    python = a.python or (sys.executable if WINDOWS else 'python3')
    form = a.form or ('exec' if WINDOWS else 'shell')
    skip = [s.strip() for s in a.skip.split(',') if s.strip()]

    # utf-8-sig on the way in, utf-8 on the way out: a settings.json written by a PowerShell script, or by any
    # Windows editor, can carry a byte order mark that plain utf-8 refuses to decode. We tolerate one and drop it.
    settings = json.load(open(a.settings, encoding='utf-8-sig')) if os.path.exists(a.settings) else {}
    hooks = settings.get('hooks', {})
    wanted = build(os.path.abspath(a.repo), python, form, skip)

    removed = 0
    for event, groups in list(hooks.items()):
        keep = []
        for g in groups:
            if any(MARKER in (h.get('command') or '') or any(MARKER in x for x in (h.get('args') or []))
                   for h in g.get('hooks', [])):
                removed += 1
            else:
                keep.append(g)
        hooks[event] = keep
    for event, groups in wanted.items():
        hooks.setdefault(event, []).extend(groups)
    hooks = {k: v for k, v in hooks.items() if v}

    print(f'    interpreter: {python}')
    print(f'    form:        {form}')
    for event, matcher, name, timeout in HOOKS:
        state = 'skipped' if skipped(skip, event, name) else f'({timeout}s)'
        print(f'    {event:<17} {matcher or "*":<7} {name}  {state}')
    if removed:
        print(f'    (replaced {removed} previous tokenwise entr{"y" if removed == 1 else "ies"})')
    others = sum(len(v) for v in hooks.values()) - sum(len(v) for v in wanted.values())
    print(f'    other tools\' hooks left untouched: {max(others, 0)} group(s)')

    if a.dry_run:
        print('    dry run: settings.json not written')
        return
    settings['hooks'] = hooks
    with open(a.settings, 'w', encoding='utf-8') as fh:
        json.dump(settings, fh, indent=2)
        fh.write('\n')
    json.load(open(a.settings, encoding='utf-8-sig'))   # fail loudly rather than leave a corrupt settings file
    print('    settings.json updated and re-parsed OK')


if __name__ == '__main__':
    main()
