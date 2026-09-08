#!/usr/bin/env python3
"""where-is — a symbol index so the model asks "where is X" and gets file:line back instead of reading files.

  whereis.py build [REPO ...]     (re)index repos incrementally by mtime; default = repos listed in repos.txt or cwd
  whereis.py NAME [--repo R] [--kind class|method|function|type|table] [--limit 15]

Regex indexers, no parsers: Java, Kotlin, TS/JS, Python, Go, SQL. Good enough to land on the right file and line.
"""
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, 'symbols.db')
REPOS_FILE = os.path.join(ROOT, 'repos.txt')
SKIP_DIRS = {'.git', 'node_modules', 'target', 'build', 'dist', '.venv', 'venv', '.next', '.claude', 'out', 'coverage',
             '__pycache__', '.idea', '.gradle', 'logs'}

PATTERNS = {
    '.java': [
        (re.compile(r'^\s*(?:(?:public|private|protected|static|final|abstract|sealed|non-sealed)\s+)*(class|interface|enum|record|@interface)\s+(\w+)'), None),
        (re.compile(r'^\s*(?:(?:public|private|protected|static|final|synchronized|abstract|default|native)\s+)+(?:<[^>]+>\s+)?[\w<>\[\],.?\s]+?\s+(\w+)\s*\([^;]*$'), 'method'),
    ],
    '.kt': [(re.compile(r'^\s*(?:(?:public|private|internal|open|data|sealed|abstract|suspend|override|inline)\s+)*(class|interface|object|fun|typealias)\s+(?:<[^>]+>\s+)?(\w+)'), None)],
    '.py': [(re.compile(r'^\s*(?:async\s+)?(def|class)\s+(\w+)'), None)],
    '.go': [(re.compile(r'^(func)\s+(?:\([^)]*\)\s*)?(\w+)'), None), (re.compile(r'^(type)\s+(\w+)'), None)],
    '.sql': [(re.compile(r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNIQUE\s+)?(TABLE|VIEW|INDEX|FUNCTION|PROCEDURE|TRIGGER|TYPE|MATERIALIZED VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.]+)', re.I), None)],
}
TS = [
    (re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?(function|class|interface|type|enum|namespace)\s+(\w+)'), None),
    (re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*(?::\s*[^=]+)?=>'), 'function'),
    (re.compile(r'^\s*(?:export\s+)?const\s+(\w+)\s*(?::[^=]+)?='), 'const'),
    (re.compile(r'^\s*(?:public|private|protected|static|async|readonly|\s)*\s*(\w+)\s*\([^)]*\)\s*(?::\s*[\w<>\[\]|, .]+)?\s*\{\s*$'), 'method'),
]
for ext in ('.ts', '.tsx', '.js', '.jsx', '.mjs'):
    PATTERNS[ext] = TS
JAVA_NOISE = {'return', 'new', 'if', 'else', 'for', 'while', 'switch', 'catch', 'try', 'throw', 'super', 'this'}


def con():
    c = sqlite3.connect(DB, timeout=10)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, repo TEXT, mtime REAL);
    CREATE TABLE IF NOT EXISTS symbols (id INTEGER PRIMARY KEY, repo TEXT, path TEXT, line INTEGER, kind TEXT, name TEXT, name_lc TEXT);
    CREATE INDEX IF NOT EXISTS sym_name ON symbols(name_lc);
    CREATE INDEX IF NOT EXISTS sym_path ON symbols(path);
    """)
    return c


def index_file(c, repo, path):
    ext = os.path.splitext(path)[1].lower()
    pats = PATTERNS.get(ext)
    if not pats:
        return 0
    try:
        with open(path, 'r', errors='replace') as fh:
            lines = fh.readlines()
    except OSError:
        return 0
    c.execute('DELETE FROM symbols WHERE path=?', (path,))
    n = 0
    for i, line in enumerate(lines, 1):
        if len(line) > 400:
            continue
        for rx, forced_kind in pats:
            m = rx.search(line)
            if not m:
                continue
            groups = m.groups()
            if forced_kind:
                name, kind = groups[-1], forced_kind
            else:
                kind, name = groups[0].lower(), groups[1]
            if ext == '.java' and kind == 'method' and (name in JAVA_NOISE or name[0].isupper()):
                continue
            if ext in TS_EXTS and kind == 'method' and name in ('if', 'for', 'while', 'switch', 'catch', 'function', 'return', 'constructor'):
                if name != 'constructor':
                    continue
            c.execute('INSERT INTO symbols(repo, path, line, kind, name, name_lc) VALUES (?,?,?,?,?,?)', (repo, path, i, kind, name, name.lower()))
            n += 1
            break
    return n


TS_EXTS = {'.ts', '.tsx', '.js', '.jsx', '.mjs'}


def build(repos):
    c = con()
    t0 = time.time()
    stats = {'files': 0, 'symbols': 0, 'skipped': 0}
    for repo in repos:
        repo = os.path.abspath(os.path.expanduser(repo))
        name = os.path.basename(repo)
        seen = set()
        for dp, dn, fn in os.walk(repo):
            dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith('.')]
            for f in fn:
                if os.path.splitext(f)[1].lower() not in PATTERNS:
                    continue
                p = os.path.join(dp, f)
                seen.add(p)
                try:
                    mt = os.path.getmtime(p)
                except OSError:
                    continue
                row = c.execute('SELECT mtime FROM files WHERE path=?', (p,)).fetchone()
                if row and row[0] == mt:
                    stats['skipped'] += 1
                    continue
                stats['symbols'] += index_file(c, name, p)
                stats['files'] += 1
                c.execute('INSERT INTO files(path, repo, mtime) VALUES (?,?,?) ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime', (p, name, mt))
        # drop files that vanished
        for (p,) in c.execute('SELECT path FROM files WHERE repo=?', (name,)).fetchall():
            if p not in seen:
                c.execute('DELETE FROM symbols WHERE path=?', (p,))
                c.execute('DELETE FROM files WHERE path=?', (p,))
        c.commit()
    stats['seconds'] = int(time.time() - t0)
    stats['total_symbols'] = c.execute('SELECT COUNT(*) FROM symbols').fetchone()[0]
    return stats


def query(name, repo=None, kind=None, limit=15):
    c = con()
    lc = name.lower()
    sql = 'SELECT repo, path, line, kind, name FROM symbols WHERE (name_lc=? OR name_lc LIKE ? OR name_lc LIKE ?) '
    args = [lc, lc + '%', '%' + lc + '%']
    if repo:
        sql += 'AND repo=? '
        args.append(repo)
    if kind:
        sql += 'AND kind=? '
        args.append(kind)
    sql += 'ORDER BY CASE WHEN name_lc=? THEN 0 WHEN name_lc LIKE ? THEN 1 ELSE 2 END, kind, path LIMIT ?'
    args += [lc, lc + '%', limit]
    rows = c.execute(sql, args).fetchall()
    home = os.path.expanduser('~')
    for repo_, path, line, kind_, nm in rows:
        print(f'{path.replace(home, "~")}:{line}  {kind_}  {nm}')
    if not rows:
        print(f'no symbol like "{name}" in the index (rebuild: whereis.py build <repo>)')


def default_repos():
    if os.path.exists(REPOS_FILE):
        return [l.strip() for l in open(REPOS_FILE) if l.strip() and not l.startswith('#')]
    return [os.getcwd()]


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a or a[0] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)
    if a[0] == 'build':
        print(build(a[1:] or default_repos()))
        sys.exit(0)
    repo = kind = None
    limit = 15
    name = a[0]
    i = 1
    while i < len(a):
        if a[i] == '--repo':
            repo = a[i + 1]; i += 2
        elif a[i] == '--kind':
            kind = a[i + 1]; i += 2
        elif a[i] == '--limit':
            limit = int(a[i + 1]); i += 2
        else:
            i += 1
    query(name, repo, kind, limit)
