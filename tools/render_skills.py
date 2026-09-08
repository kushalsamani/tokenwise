#!/usr/bin/env python3
"""Render skill templates with this checkout's real path, into ~/.claude/skills. Run by install.sh."""
import os, sys, shutil
repo = os.path.abspath(sys.argv[1]); dest = os.path.expanduser('~/.claude/skills')
for sk in ('where-is', 'ledger'):
    src = os.path.join(repo, 'skills', sk, 'SKILL.md')
    out = os.path.join(dest, sk)
    if os.path.islink(out): os.unlink(out)
    elif os.path.isdir(out): shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, 'SKILL.md'), 'w').write(open(src).read().replace('{{TOKENWISE}}', repo))
    print(f'    /{sk}')
