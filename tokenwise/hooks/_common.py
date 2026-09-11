"""Shared bits for tokenwise hooks. Every hook: read stdin JSON, never raise, never block, log what it did.
Disable all handlers at once with TOKENWISE_OFF=1 (the harness uses this for the control arm).

Everything here is platform-neutral: the OS is detected at runtime, so the same checkout works on macOS,
Linux and Windows. Only two things actually differ between them, and both live in this file: how a desktop
notification is raised, and how stdout is encoded.
"""
import json
import os
import subprocess
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the tokenwise package; ledger.db lives here
REPO = os.path.dirname(ROOT)                                          # the checkout, where install.sh lives
CONFIG = os.path.join(REPO, 'config.local.json')

IS_WINDOWS = os.name == 'nt'
IS_MAC = sys.platform == 'darwin'
IS_LINUX = not IS_WINDOWS and not IS_MAC


def utf8_stdout():
    """Windows consoles default to a legacy code page, and the reports contain arrows and maths signs.
    Without this, `ledger.py report` dies with UnicodeEncodeError on an otherwise healthy machine."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


_cfg_cache = None


def _config():
    """Optional local overrides, so a machine can be tuned without exporting environment variables.

    Environment variables are awkward on Windows: a hook is launched by the Claude Code process and inherits
    whatever that process was started with, not your shell. This file is gitignored -- it is your machine's
    settings, not the project's.
    """
    global _cfg_cache
    if _cfg_cache is None:
        try:
            with open(CONFIG, encoding='utf-8') as fh:
                _cfg_cache = json.load(fh) or {}
        except Exception:
            _cfg_cache = {}
    return _cfg_cache


def setting(key, default=None):
    """A tokenwise setting: the environment wins, then config.local.json, then the built-in default."""
    v = os.environ.get(key)
    if v is not None and v != '':
        return v
    v = _config().get(key)
    return default if v is None else str(v)


DB = setting('TOKENWISE_DB', os.path.join(ROOT, 'ledger.db'))


def disabled():
    return bool(os.environ.get('TOKENWISE_OFF') or os.environ.get('RECALL_JOB'))


def read_stdin():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def respond(event, additional_context=None, updated_input=None):
    out = {'hookEventName': event}
    if additional_context:
        out['additionalContext'] = additional_context
    if updated_input is not None:
        out['updatedInput'] = updated_input
    print(json.dumps({'hookSpecificOutput': out}))


def _con():
    con = sqlite3.connect(DB, timeout=5)
    con.execute('PRAGMA busy_timeout=3000')
    con.execute('CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY, ts TEXT, session_id TEXT, handler TEXT, '
                'detail TEXT, est_saved INTEGER DEFAULT 0)')
    con.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
    return con


def log_action(session_id, handler, detail, est_saved=0):
    try:
        con = _con()
        con.execute('INSERT INTO actions(ts, session_id, handler, detail, est_saved) VALUES (?,?,?,?,?)',
                    (time.strftime('%Y-%m-%dT%H:%M:%S'), session_id or '', handler, str(detail)[:2000], int(est_saved or 0)))
        con.commit()
        con.close()
    except Exception:
        pass


def meta_get(key, default=None):
    try:
        con = _con()
        r = con.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        con.close()
        return r[0] if r else default
    except Exception:
        return default


def meta_set(key, value):
    try:
        con = _con()
        con.execute('INSERT INTO meta(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
        con.commit()
        con.close()
    except Exception:
        pass


CLAUDE_CONFIG = os.path.expanduser('~/.claude.json')


def note_account(session_id):
    """Record which Claude account a session belongs to, once per session.

    Claude Code hooks are per OS USER, not per Claude account: one settings file, one transcript tree, one
    ledger, shared by every account signed in on the machine. Without this the report cannot tell you which
    account spent what -- personal and work usage arrive as one undifferentiated pile.

    Reads only ~/.claude.json (your own local config, already on disk) and writes the label into your own local
    ledger. Nothing is transmitted anywhere. Turn it off with TOKENWISE_ACCOUNTS=0.
    """
    if not session_id or os.environ.get('TOKENWISE_ACCOUNTS') == '0':
        return
    try:
        con = _con()
        con.execute('CREATE TABLE IF NOT EXISTS session_accounts (session_id TEXT PRIMARY KEY, email TEXT, '
                    'account_uuid TEXT, label TEXT, seen TEXT)')
        if con.execute('SELECT 1 FROM session_accounts WHERE session_id=?', (session_id,)).fetchone():
            con.close()
            return
        with open(CLAUDE_CONFIG, encoding='utf-8') as fh:
            acc = (json.load(fh) or {}).get('oauthAccount') or {}
        email = acc.get('emailAddress')
        org = (acc.get('organizationName') or '').strip()
        label = org or (email or 'unknown').split('@')[-1].split('.')[0]
        con.execute('INSERT OR IGNORE INTO session_accounts(session_id, email, account_uuid, label, seen) '
                    'VALUES (?,?,?,?,?)',
                    (session_id, email, acc.get('accountUuid'), label, time.strftime('%Y-%m-%dT%H:%M:%S')))
        con.commit()
        con.close()
    except Exception:
        pass


# --------------------------------------------------------------------------- desktop notification
NOTIFY_PS1 = os.path.join(ROOT, 'notify.ps1')


def notify(title, message):
    """One desktop notification, on whichever OS this is. Never raises, never modal: there is nothing to
    click, and a dialog would hold the hook open until it was dismissed.

    macOS    osascript, as before.
    Windows  a toast through notify.ps1, which MANIFEST.sha256 hashes like every other file the installer
             wires, so the script that runs is the script that was reviewed.
    Linux    notify-send when it exists, silence when it does not.

    TOKENWISE_NOTIFY=0 turns it off everywhere.
    """
    if setting('TOKENWISE_NOTIFY', '1') == '0':
        return
    msg = (message or '').replace('"', "'")[:230]
    ttl = (title or 'tokenwise').replace('"', "'")[:60]
    try:
        if IS_MAC:
            subprocess.run(['osascript', '-e', f'display notification "{msg}" with title "{ttl}" sound name "Ping"'],
                           capture_output=True, timeout=8)
        elif IS_WINDOWS:
            subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', NOTIFY_PS1, ttl, msg],
                           capture_output=True, timeout=12,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        else:
            subprocess.run(['notify-send', ttl, msg], capture_output=True, timeout=8)
    except Exception as ex:
        debug(f'notify failed: {ex}')


def debug(msg):
    if os.environ.get('TOKENWISE_DEBUG'):
        with open(os.path.join(ROOT, 'hooks.debug.log'), 'a', encoding='utf-8') as fh:
            fh.write(f'{time.strftime("%F %T")} {msg}\n')
