#!/usr/bin/env python3
"""audit.py — static safety gate for tokenwise hooks. Runs before every install, and in CI on every pull request.

WHY THIS EXISTS. Claude Code hooks are remote code execution by design: once wired into settings.json they run
automatically, with your privileges, on every prompt and every tool call, with no further consent. That is fine for
code you wrote. It is not fine for code that arrived through a pull request from a stranger. This file is the gate
between those two situations.

It is a static check, not a sandbox, so it cannot prove safety. What it does is make the dangerous shapes impossible
to merge quietly: a contributor who needs one of them has to change this file too, in the same diff, where a
reviewer will see it.

  python3 audit.py                 audit the repo's hook and tool files
  python3 audit.py --path DIR      audit an installed copy
  python3 audit.py --manifest      also verify MANIFEST.sha256 matches the files on disk
  python3 audit.py --write-manifest    regenerate MANIFEST.sha256 (maintainer only)

Exit code 0 = clean, 1 = blocking finding, 2 = usage error.
"""
import argparse
import ast
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, 'MANIFEST.sha256')

# --------------------------------------------------------------------------- policy
# Modules a hook may never import. Hooks are local, offline, and fast by contract.
BANNED_IMPORTS = {
    'socket', 'ssl', 'http', 'httplib', 'urllib', 'urllib2', 'urllib3', 'requests', 'httpx', 'aiohttp',
    'ftplib', 'smtplib', 'telnetlib', 'poplib', 'imaplib', 'xmlrpc', 'webbrowser',
    'pickle', 'cPickle', 'marshal', 'shelve', 'dill',            # deserialisation = code execution
    'ctypes', 'cffi', 'multiprocessing', 'pty', 'fcntl', 'resource',
    'importlib', 'imp', 'runpy', 'pkgutil',                      # dynamic loading
}
# Callables a hook may never use.
BANNED_CALLS = {
    'eval', 'exec', 'compile', 'execfile', '__import__', 'breakpoint', 'input',
    'os.system', 'os.popen', 'os.execv', 'os.execve', 'os.execvp', 'os.spawnl', 'os.spawnv', 'os.fork',
    'subprocess.call', 'subprocess.check_call', 'subprocess.check_output', 'subprocess.getoutput',
    'subprocess.getstatusoutput', 'subprocess.Popen',
    'shutil.rmtree', 'os.remove', 'os.unlink', 'os.rmdir', 'os.removedirs', 'os.chmod', 'os.chown',
    'setattr', 'delattr', 'globals', 'locals', 'vars', 'memoryview',
}
# Only these programs may be launched, and only through subprocess.run with a literal list.
ALLOWED_PROGRAMS = {'osascript'}          # sys.executable is allowed separately
# Strings that betray intent regardless of how they are assembled.
BANNED_SUBSTRINGS = [
    ('permissiondecision', 'a hook must never make a permission decision: that would auto-approve tool calls '
                           'the user never saw'),
    ('curl ', 'network access from a hook'),
    ('wget ', 'network access from a hook'),
    ('nc -', 'network access from a hook'),
    ('/etc/passwd', 'reading system credential files'),
    ('id_rsa', 'reading private keys'),
    ('.ssh/', 'reading the ssh directory'),
    ('security find-generic-password', 'reading the macOS keychain'),
    ('base64 -d', 'decoding an opaque payload'),
    ('.aws/credentials', 'reading cloud credentials'),
    ('settings.json', 'only the installer may touch settings.json, never a hook'),
]
MAX_BYTES = 60_000            # a hook that needs more than this is not a hook


class Finding:
    def __init__(self, path, line, rule, detail, blocking=True):
        self.path, self.line, self.rule, self.detail, self.blocking = path, line, rule, detail, blocking

    def __str__(self):
        mark = 'BLOCK' if self.blocking else 'warn '
        rel = os.path.relpath(self.path, HERE)
        return f'  {mark}  {rel}:{self.line}  [{self.rule}] {self.detail}'


def _dotted(node):
    """Best-effort dotted name for a call target."""
    bits = []
    while isinstance(node, ast.Attribute):
        bits.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        bits.append(node.id)
    return '.'.join(reversed(bits))


def audit_source(path, src):
    out = []
    if len(src.encode()) > MAX_BYTES:
        out.append(Finding(path, 1, 'size', f'file is {len(src.encode())} bytes, over the {MAX_BYTES} limit'))
    low = src.lower()
    for needle, why in BANNED_SUBSTRINGS:
        if needle in low:
            line = low[:low.index(needle)].count('\n') + 1
            out.append(Finding(path, line, 'forbidden-string', f'{needle.strip()!r}: {why}'))
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        out.append(Finding(path, e.lineno or 1, 'syntax', str(e)))
        return out

    for node in ast.walk(tree):
        # imports
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split('.')[0]
                if root in BANNED_IMPORTS:
                    out.append(Finding(path, node.lineno, 'import', f'`{a.name}` is not allowed in a hook'))
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or '').split('.')[0]
            if root in BANNED_IMPORTS:
                out.append(Finding(path, node.lineno, 'import', f'`{node.module}` is not allowed in a hook'))
        # calls
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in BANNED_CALLS:
                out.append(Finding(path, node.lineno, 'call', f'`{name}()` is not allowed in a hook'))
            if name == 'subprocess.run':
                for kw in node.keywords:
                    if kw.arg == 'shell' and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                        out.append(Finding(path, node.lineno, 'shell', 'subprocess.run(shell=True) is never allowed'))
                if not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple)):
                    out.append(Finding(path, node.lineno, 'subprocess',
                                       'subprocess.run needs a literal argv list, not a string or variable'))
                else:
                    first = node.args[0].elts[0] if node.args[0].elts else None
                    prog = None
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        prog = os.path.basename(first.value)
                    elif isinstance(first, ast.Attribute) and _dotted(first) == 'sys.executable':
                        prog = 'sys.executable'
                    if prog is None:
                        out.append(Finding(path, node.lineno, 'subprocess',
                                           'the program must be a literal string or sys.executable'))
                    elif prog not in ALLOWED_PROGRAMS and prog != 'sys.executable':
                        out.append(Finding(path, node.lineno, 'subprocess',
                                           f'`{prog}` is not in the allowlist {sorted(ALLOWED_PROGRAMS)}'))
            # writing files outside the tool's own directory
            if name == 'open':
                mode = None
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                for kw in node.keywords:
                    if kw.arg == 'mode' and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                if isinstance(mode, str) and any(m in mode for m in ('w', 'a', 'x', '+')):
                    arg = node.args[0] if node.args else None
                    literal = isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    if literal and not arg.value.startswith(('/tmp/', '/private/tmp/')):
                        out.append(Finding(path, node.lineno, 'write',
                                           f'writes to a fixed path outside the tool directory: {arg.value}',
                                           blocking=False))
    return out


def files_to_audit(root):
    out = []
    for sub in ('tokenwise',):
        base = os.path.join(root, sub)
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d != '__pycache__']
            for f in sorted(fn):
                if f.endswith('.py'):
                    out.append(os.path.join(dp, f))
    return out


def manifest_lines(root):
    lines = []
    for f in files_to_audit(root):
        h = hashlib.sha256(open(f, 'rb').read()).hexdigest()
        lines.append(f'{h}  {os.path.relpath(f, root)}')
    for extra in ('install.sh', 'uninstall.sh', 'audit.py', 'harness/run.sh'):
        p = os.path.join(root, extra)
        if os.path.exists(p):
            lines.append(f'{hashlib.sha256(open(p, "rb").read()).hexdigest()}  {extra}')
    return sorted(lines)


def check_manifest(root):
    if not os.path.exists(MANIFEST):
        return [Finding(MANIFEST, 1, 'manifest', 'MANIFEST.sha256 is missing; run --write-manifest', blocking=False)]
    want = {l.split('  ', 1)[1]: l.split('  ', 1)[0] for l in
            open(MANIFEST).read().splitlines() if l.strip() and not l.startswith('#')}
    have = {l.split('  ', 1)[1]: l.split('  ', 1)[0] for l in manifest_lines(root)}
    out = []
    for rel, h in have.items():
        if rel not in want:
            out.append(Finding(os.path.join(root, rel), 1, 'manifest', 'file is not in MANIFEST.sha256'))
        elif want[rel] != h:
            out.append(Finding(os.path.join(root, rel), 1, 'manifest', 'contents differ from MANIFEST.sha256'))
    for rel in want:
        if rel not in have:
            out.append(Finding(os.path.join(root, rel), 1, 'manifest', 'listed in MANIFEST.sha256 but missing'))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--path', default=HERE)
    ap.add_argument('--manifest', action='store_true', help='also verify MANIFEST.sha256')
    ap.add_argument('--write-manifest', action='store_true', help='regenerate MANIFEST.sha256')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    root = os.path.abspath(os.path.expanduser(a.path))

    if a.write_manifest:
        body = ('# sha256 of every file the installer will wire or execute.\n'
                '# Regenerate with: python3 audit.py --write-manifest\n' + '\n'.join(manifest_lines(root)) + '\n')
        open(MANIFEST, 'w').write(body)
        print(f'wrote {MANIFEST} ({len(manifest_lines(root))} files)')
        return 0

    findings = []
    files = files_to_audit(root)
    for f in files:
        findings += audit_source(f, open(f, encoding='utf-8', errors='replace').read())
    if a.manifest:
        findings += check_manifest(root)

    blocking = [f for f in findings if f.blocking]
    if not a.quiet:
        print(f'tokenwise audit: {len(files)} file(s) checked')
        for f in findings:
            print(f)
    if blocking:
        print(f'\nFAILED: {len(blocking)} blocking finding(s). Nothing was installed.')
        print('If a finding is a deliberate, reviewed exception, it must be added to audit.py in the same pull '
              'request, so a human sees the policy change next to the code that needs it.')
        return 1
    if not a.quiet:
        warns = len(findings)
        print(f'PASSED{f" with {warns} warning(s)" if warns else ""}.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
