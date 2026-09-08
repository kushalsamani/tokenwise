"""Shared bits for tokenwise hooks. Every hook: read stdin JSON, never raise, never block, log what it did.
Disable all handlers at once with TOKENWISE_OFF=1 (the harness uses this for the control arm)."""
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get('TOKENWISE_DB', os.path.join(ROOT, 'ledger.db'))


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


def debug(msg):
    if os.environ.get('TOKENWISE_DEBUG'):
        with open(os.path.join(ROOT, 'hooks.debug.log'), 'a') as fh:
            fh.write(f'{time.strftime("%F %T")} {msg}\n')
