#!/usr/bin/env python3
"""Render skill templates into ~/.claude/skills with this checkout's real path and this machine's interpreter.

Two placeholders: {{TOKENWISE}} is the repo, {{PY}} is how to launch Python here. `python3` is right on macOS and
Linux and wrong on Windows, where it is usually a Microsoft Store stub, so the installer passes the interpreter it
actually used to wire the hooks and the skill tells the model the same command that works.

    render_skills.py <repo> [python]
"""
import os, shutil, sys

repo = os.path.abspath(sys.argv[1])
python = sys.argv[2] if len(sys.argv) > 2 else ('python3' if os.name != 'nt' else sys.executable)
if ' ' in python:
    python = f'"{python}"'
dest = os.path.expanduser('~/.claude/skills')

for sk in ('where-is', 'ledger'):
    src = os.path.join(repo, 'skills', sk, 'SKILL.md')
    out = os.path.join(dest, sk)
    if os.path.islink(out):
        os.unlink(out)
    elif os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)
    with open(src, encoding='utf-8') as fh:
        body = fh.read()
    body = body.replace('{{TOKENWISE}}', repo.replace('\\', '/')).replace('{{PY}}', python)
    with open(os.path.join(out, 'SKILL.md'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(body)
    print(f'    /{sk}')
