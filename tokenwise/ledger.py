#!/usr/bin/env python3
"""tokenwise ledger — where the tokens go, from the transcripts Claude Code already writes. Standalone; stdlib only.

  ledger.py ingest [-v]                incremental parse of ~/.claude/projects/**/*.jsonl into ledger.db
  ledger.py report [--today|--week|--days N|--session ID] [--no-ingest]
  ledger.py actions [--days N]         what the handlers did and what they estimate they saved

Sessions are attributed to the Claude account that was active when they started, so a machine shared by a personal
and a work account can see which spent what. See _common.note_account; disable with TOKENWISE_ACCOUNTS=0.

Cost proxy = list price per model (input ×1, cache write ×1.25, cache read ×0.10 — Fable 5.1 cache read $0.25/M,
output ×5). The weekly-limit percentage is not readable locally; /usage stays the arbiter. This shows the SHAPE:
context per turn, turns per prompt, which tools inflate results, and what the handlers changed.
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get('TOKENWISE_DB', os.path.join(ROOT, 'ledger.db'))
PROJECTS = os.path.expanduser('~/.claude/projects')

# $ per 1M tokens: (input, output, cache_read) — cache write = input × 1.25
PRICES = {
    'claude-fable-5-1': (10.0, 50.0, 0.25), 'claude-fable-5': (10.0, 50.0, 1.0),
    'claude-opus-5': (5.0, 25.0, 0.5), 'claude-opus-4-8': (5.0, 25.0, 0.5), 'claude-opus-4-7': (5.0, 25.0, 0.5),
    'claude-opus-4-6': (5.0, 25.0, 0.5), 'claude-sonnet-5': (2.0, 10.0, 0.2), 'claude-sonnet-4-6': (3.0, 15.0, 0.3),
    'claude-haiku-4-5': (1.0, 5.0, 0.1),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, cursor INTEGER DEFAULT 0, session_id TEXT, project TEXT, is_subagent INTEGER);
CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, session_id TEXT, project TEXT, is_subagent INTEGER, ts TEXT, model TEXT,
  effort TEXT, tok_in INTEGER, tok_cc INTEGER, tok_cr INTEGER, tok_out INTEGER, ctx INTEGER, n_tools INTEGER, request_id TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS turns_ts ON turns(ts);
CREATE TABLE IF NOT EXISTS tool_calls (id INTEGER PRIMARY KEY, session_id TEXT, ts TEXT, tool_use_id TEXT UNIQUE, name TEXT,
  input_chars INTEGER, result_chars INTEGER DEFAULT 0, request_id TEXT);
CREATE INDEX IF NOT EXISTS tool_calls_ts ON tool_calls(ts);
CREATE TABLE IF NOT EXISTS prompts (id INTEGER PRIMARY KEY, session_id TEXT, project TEXT, ts TEXT, uuid TEXT UNIQUE, head TEXT, words INTEGER);
CREATE INDEX IF NOT EXISTS prompts_ts ON prompts(ts);
CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY, ts TEXT, session_id TEXT, handler TEXT, detail TEXT, est_saved INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def con():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000')
    c.executescript(SCHEMA)
    return c


def price(model, tin, tcc, tcr, tout):
    base = None
    for k, v in PRICES.items():
        if model and model.startswith(k):
            base = v
            break
    if not base:
        base = (5.0, 25.0, 0.5)
    i, o, cr = base
    return (tin * i + tcc * i * 1.25 + tcr * cr + tout * o) / 1e6


# ----------------------------------------------------------------- ingest
def _files():
    for dp, dn, fn in os.walk(PROJECTS):
        if os.path.basename(dp) == 'memory':
            dn[:] = []
            continue
        for f in fn:
            if f.endswith('.jsonl'):
                yield os.path.join(dp, f)


def _ids(path):
    rel = os.path.relpath(path, PROJECTS).split(os.sep)
    if len(rel) == 2:
        return rel[0], rel[1][:-6], 0
    return rel[0], os.path.basename(path)[:-6], 1


def ingest_file(c, path, verbose=False):
    project, sid, is_sub = _ids(path)
    row = c.execute('SELECT cursor FROM files WHERE path=?', (path,)).fetchone()
    cursor = row['cursor'] if row else 0
    size = os.path.getsize(path)
    if cursor >= size:
        return 0
    settled = time.time() - os.path.getmtime(path) > 600
    n = 0
    end = cursor
    last_rid = None
    with open(path, 'rb') as fh:
        fh.seek(cursor)
        while True:
            line = fh.readline()
            if not line:
                break
            if not line.endswith(b'\n') and not settled:
                break
            end = fh.tell()
            if b'"type":"user"' not in line and b'"type":"assistant"' not in line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            ts = e.get('timestamp') or ''
            m = e.get('message') or {}
            if e.get('type') == 'assistant':
                u = m.get('usage') or {}
                rid = e.get('requestId')
                tools = [b for b in (m.get('content') or []) if isinstance(b, dict) and b.get('type') == 'tool_use']
                for b in tools:
                    c.execute('INSERT OR IGNORE INTO tool_calls(session_id, ts, tool_use_id, name, input_chars, request_id) VALUES (?,?,?,?,?,?)',
                              (sid, ts, b.get('id'), b.get('name', '?'), len(json.dumps(b.get('input') or {})), rid))
                if rid and rid != last_rid and u:
                    last_rid = rid
                    tin, tcc, tcr, tout = (u.get('input_tokens', 0) or 0, u.get('cache_creation_input_tokens', 0) or 0,
                                           u.get('cache_read_input_tokens', 0) or 0, u.get('output_tokens', 0) or 0)
                    c.execute('INSERT OR IGNORE INTO turns(session_id, project, is_subagent, ts, model, effort, tok_in, tok_cc, tok_cr, tok_out, ctx, n_tools, request_id) '
                              'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                              (sid, project, is_sub, ts, m.get('model'), (e.get('effort') or {}).get('level') if isinstance(e.get('effort'), dict) else e.get('effort'),
                               tin, tcc, tcr, tout, tin + tcc + tcr, len(tools), rid))
                    n += 1
                elif rid == last_rid and tools:
                    c.execute('UPDATE turns SET n_tools=n_tools+? WHERE request_id=?', (len(tools), rid))
            elif e.get('type') == 'user' and not e.get('isMeta'):
                content = m.get('content')
                if isinstance(content, list):
                    results = [b for b in content if isinstance(b, dict) and b.get('type') == 'tool_result']
                    for b in results:
                        body = b.get('content')
                        if isinstance(body, list):
                            body = ''.join(x.get('text', '') for x in body if isinstance(x, dict))
                        c.execute('UPDATE tool_calls SET result_chars=? WHERE tool_use_id=?', (len(body or ''), b.get('tool_use_id')))
                    if results:
                        continue
                    text = '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
                else:
                    text = content if isinstance(content, str) else ''
                text = (text or '').strip()
                if text and not text.startswith('<') and not is_sub:
                    c.execute('INSERT OR IGNORE INTO prompts(session_id, project, ts, uuid, head, words) VALUES (?,?,?,?,?,?)',
                              (sid, project, ts, e.get('uuid'), re.sub(r'\s+', ' ', text)[:120], len(text.split())))
    c.execute('INSERT INTO files(path, cursor, session_id, project, is_subagent) VALUES (?,?,?,?,?) '
              'ON CONFLICT(path) DO UPDATE SET cursor=excluded.cursor', (path, size if settled else end, sid, project, is_sub))
    c.commit()
    if verbose:
        print(f'  {sid[:8]} +{n} turns', file=sys.stderr)
    return n


def ingest(verbose=False):
    c = con()
    t0 = time.time()
    n = files = 0
    for p in _files():
        try:
            n += ingest_file(c, p, verbose)
            files += 1
        except Exception as ex:
            with open(os.path.join(ROOT, 'ledger-errors.log'), 'a') as fh:
                fh.write(f'{time.strftime("%F %T")} {p}: {ex}\n')
    c.execute("INSERT INTO meta(key, value) VALUES ('last_ingest', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (time.strftime('%F %T'),))
    c.commit()
    return {'files': files, 'new_turns': n, 'seconds': int(time.time() - t0)}


# ----------------------------------------------------------------- report
def _utc(local_dt):
    """transcript timestamps are UTC with a Z suffix; compare against UTC strings"""
    return local_dt.astimezone().astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')


def _window(args):
    now = dt.datetime.now()
    if args.session:
        return None, None
    if args.today:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - dt.timedelta(days=args.days or 7)
    return _utc(start), _utc(now)


def _fmt(n):
    return f'{n / 1e6:.1f}M' if n >= 1e6 else (f'{n / 1e3:.0f}K' if n >= 1e3 else str(int(n)))


def totals(c, a, b, session=None):
    where, args = ('WHERE session_id LIKE ?', [session + '%']) if session else ("WHERE ts BETWEEN ? AND ?", [a, b])
    rows = c.execute(f'SELECT * FROM turns {where}', args).fetchall()
    t = Counter()
    cost = 0.0
    by_model = defaultdict(Counter)
    ctxs = []
    for r in rows:
        for k in ('tok_in', 'tok_cc', 'tok_cr', 'tok_out'):
            t[k] += r[k] or 0
            by_model[r['model'] or '?'][k] += r[k] or 0
        by_model[r['model'] or '?']['turns'] += 1
        cost += price(r['model'], r['tok_in'] or 0, r['tok_cc'] or 0, r['tok_cr'] or 0, r['tok_out'] or 0)
        if not r['is_subagent']:
            ctxs.append(r['ctx'] or 0)
    return rows, t, cost, by_model, ctxs


def report(args):
    c = con()
    if not args.no_ingest:
        ingest()
    a, b = _window(args)
    rows, t, cost, by_model, ctxs = totals(c, a, b, args.session)
    label = f'session {args.session}' if args.session else f'{a[:16]} → {b[:16]}'
    print(f'# tokenwise ledger — {label}')
    print(f"turns={len(rows)}  sessions={len(set(r['session_id'] for r in rows))}  "
          f"in={_fmt(t['tok_in'])} cache_write={_fmt(t['tok_cc'])} cache_read={_fmt(t['tok_cr'])} out={_fmt(t['tok_out'])}  "
          f"list-price proxy=${cost:,.0f}")
    if ctxs:
        buckets = [(0, 100e3, '<100K'), (100e3, 200e3, '100-200K'), (200e3, 400e3, '200-400K'), (400e3, 1e12, '>400K')]
        print(f'context per main-thread turn: avg={_fmt(statistics.mean(ctxs))} median={_fmt(statistics.median(ctxs))} max={_fmt(max(ctxs))}')
        crs = [(r['ctx'] or 0, r['tok_cr'] or 0) for r in rows if not r['is_subagent']]
        tot_cr = sum(x[1] for x in crs) or 1
        for lo, hi, name in buckets:
            n = sum(1 for x, _ in crs if lo <= x < hi)
            share = sum(cr for x, cr in crs if lo <= x < hi) / tot_cr
            if n:
                print(f'  {name:9s} turns={n:5d}  share of cache-read tokens={share:5.0%}')
    # per-account split: hooks are per macOS user, so one ledger can hold several Claude accounts
    try:
        acct = c.execute(
            'SELECT COALESCE(a.label, "unattributed") lab, COUNT(DISTINCT t.session_id) s, COUNT(*) n, '
            'SUM(t.tok_cr) cr, SUM(t.tok_out) o FROM turns t '
            'LEFT JOIN session_accounts a ON a.session_id = t.session_id '
            'WHERE t.ts BETWEEN ? AND ? GROUP BY lab ORDER BY cr DESC', (a, b)).fetchall()
    except Exception:
        acct = []
    if len(acct) > 1 or (acct and acct[0]['lab'] != 'unattributed'):
        print('by account:')
        for r in acct:
            print(f"  {r['lab'][:24]:24s} sessions={r['s']:4d} turns={r['n']:5d} "
                  f"cache_read={_fmt(r['cr'] or 0):>7s} out={_fmt(r['o'] or 0):>6s}")
    print('by model:')
    for m, cnt in sorted(by_model.items(), key=lambda kv: -kv[1]['tok_cr']):
        print(f"  {m:24s} turns={cnt['turns']:5d} cache_read={_fmt(cnt['tok_cr']):>7s} out={_fmt(cnt['tok_out']):>6s}")
    # turns per prompt
    if not args.session:
        pr = c.execute('SELECT session_id, ts FROM prompts WHERE ts BETWEEN ? AND ? ORDER BY session_id, ts', (a, b)).fetchall()
        tr = c.execute('SELECT session_id, ts FROM turns WHERE ts BETWEEN ? AND ? AND is_subagent=0 ORDER BY session_id, ts', (a, b)).fetchall()
        tps = []
        by_sess = defaultdict(list)
        for r in tr:
            by_sess[r['session_id']].append(r['ts'])
        pr_by_sess = defaultdict(list)
        for r in pr:
            pr_by_sess[r['session_id']].append(r['ts'])
        for sid, pts in pr_by_sess.items():
            ts_list = by_sess.get(sid, [])
            for i, p in enumerate(pts):
                nxt = pts[i + 1] if i + 1 < len(pts) else '9'
                tps.append(sum(1 for x in ts_list if p <= x < nxt))
        if tps:
            print(f'prompts={len(tps)}  turns per prompt: avg={statistics.mean(tps):.1f} median={statistics.median(tps):.0f} max={max(tps)}')
        routes = Counter()
        for r in c.execute("SELECT detail FROM actions WHERE handler='router' AND ts BETWEEN ? AND ?", (a, b)):
            routes[r['detail'].split('|')[0]] += 1
        if routes:
            print('router classes: ' + ', '.join(f'{k}={v}' for k, v in routes.most_common()))
    # tools
    where, wargs = ('WHERE session_id LIKE ?', [args.session + '%']) if args.session else ('WHERE ts BETWEEN ? AND ?', [a, b])
    tools = c.execute(f'SELECT name, COUNT(*) n, SUM(result_chars) rc, SUM(input_chars) ic FROM tool_calls {where} GROUP BY name ORDER BY rc DESC LIMIT 10', wargs).fetchall()
    if tools:
        print('tools by result size (≈tokens = chars/4):')
        for r in tools:
            print(f"  {r['name'][:28]:28s} calls={r['n']:5d} results≈{_fmt((r['rc'] or 0) / 4):>7s} inputs≈{_fmt((r['ic'] or 0) / 4):>6s}")
    # handlers
    acts = c.execute(f"SELECT handler, COUNT(*) n, SUM(est_saved) s FROM actions {where.replace('ts BETWEEN', 'ts BETWEEN')} GROUP BY handler ORDER BY n DESC", wargs).fetchall()
    if acts:
        print('handler actions:')
        for r in acts:
            print(f"  {r['handler']:24s} n={r['n']:5d} est_saved≈{_fmt(r['s'] or 0)} tokens")
    # previous period delta
    if not args.session:
        span = dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)
        pa = (dt.datetime.fromisoformat(a) - span).strftime('%Y-%m-%dT%H:%M:%S')
        prow, pt, pcost, _, pctx = totals(c, pa, a)
        if prow:
            print(f"previous equal period: turns={len(prow)} cache_read={_fmt(pt['tok_cr'])} out={_fmt(pt['tok_out'])} "
                  f"avg_ctx={_fmt(statistics.mean(pctx)) if pctx else '-'} proxy=${pcost:,.0f}  "
                  f"→ cache_read {'+' if t['tok_cr'] >= pt['tok_cr'] else ''}{(t['tok_cr'] - pt['tok_cr']) / max(pt['tok_cr'], 1):.0%}")


def compare(args):
    """Same-shaped metrics before and after a split point (local time 'YYYY-MM-DD HH:MM')."""
    c = con()
    if not args.no_ingest:
        ingest()
    split_local = dt.datetime.strptime(args.split, '%Y-%m-%d %H:%M')
    split = _utc(split_local)
    days = args.days or 7
    a0 = _utc(split_local - dt.timedelta(days=days))
    now = _utc(dt.datetime.now())
    print(f'# tokenwise compare — split at {args.split} local')

    def block(name, a, b):
        rows, t, cost, by_model, ctxs = totals(c, a, b)
        main = [r for r in rows if not r['is_subagent']]
        pr = c.execute('SELECT session_id, ts FROM prompts WHERE ts BETWEEN ? AND ? ORDER BY session_id, ts', (a, b)).fetchall()
        tr = defaultdict(list)
        for r in main:
            tr[r['session_id']].append(r['ts'])
        pb = defaultdict(list)
        for r in pr:
            pb[r['session_id']].append(r['ts'])
        tps = []
        for sid, pts in pb.items():
            for i, p in enumerate(pts):
                nxt = pts[i + 1] if i + 1 < len(pts) else '9'
                tps.append(sum(1 for x in tr.get(sid, []) if p <= x < nxt))
        hrs = max((dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).total_seconds() / 3600, 0.01)
        big = sum(r['tok_cr'] or 0 for r in main if (r['ctx'] or 0) >= 400e3) / max(sum(r['tok_cr'] or 0 for r in main), 1)
        print(f"## {name}: {a[:16]}Z → {b[:16]}Z ({hrs:.1f} h)")
        print(f"  turns={len(rows)} (main {len(main)})  prompts={len(pr)}  turns/prompt median={statistics.median(tps) if tps else 0:.0f} avg={statistics.mean(tps) if tps else 0:.1f}")
        print(f"  cache_read={_fmt(t['tok_cr'])}  out={_fmt(t['tok_out'])}  proxy=${cost:,.0f}   per hour: cache_read={_fmt(t['tok_cr'] / hrs)} turns={len(rows) / hrs:.0f}")
        if ctxs:
            print(f"  ctx/turn avg={_fmt(statistics.mean(ctxs))} median={_fmt(statistics.median(ctxs))}  share of cache_read from ctx>=400K: {big:.0%}")
        acts = c.execute('SELECT handler, COUNT(*) n, SUM(est_saved) s FROM actions WHERE ts BETWEEN ? AND ? GROUP BY handler', (a.replace('T', ' ')[:19].replace(' ', 'T'), b)).fetchall()
        if acts:
            print('  handlers: ' + ', '.join(f"{r['handler']}={r['n']}" for r in acts))
    block('BEFORE', a0, split)
    block('AFTER ', split, now)
    print('Caveat: AFTER covers only sessions since the split; a session started before it keeps its old model window.')


def waste(args):
    """Carry cost: what a tool result actually costs once every later turn re-sends it.

    THE EQUATION. A session's bill is the sum of the context at every turn. Context at turn N is everything added in
    turns 1..N-1. So X tokens entering at turn N cost X * (T - N), where T is the session's last turn -- not X.
    A 40K-token file read at turn 10 of a 200-turn session costs 7.6M cache-read tokens, not 40K.

    Two consequences, both independent of the context WINDOW (a 1M window changes no term above):
      1. the same blob is far more expensive early in a session than near its end;
      2. the only ways to read a lot without paying the carry are to read it in a context that is thrown away
         (a subagent) or to reset the accumulator at a task boundary (/clear, /compact).
    """
    c = con()
    if not args.no_ingest:
        ingest()
    a, b = _window(args)
    sql = (
        "SELECT t.session_id, t.name, t.ts, t.result_chars, "
        "(SELECT COUNT(*) FROM turns u WHERE u.session_id=t.session_id AND u.is_subagent=0 AND u.ts>t.ts) AS remaining "
        "FROM tool_calls t WHERE t.ts BETWEEN ? AND ? AND t.result_chars > 0"
    )
    rows = c.execute(sql, (a, b)).fetchall()
    if not rows:
        print('no tool calls in window')
        return
    by_tool = defaultdict(lambda: [0, 0.0, 0.0])          # calls, carry, raw
    items = []
    for r in rows:
        raw = (r['result_chars'] or 0) / 4
        carry = raw * (r['remaining'] or 0)
        by_tool[r['name']][0] += 1
        by_tool[r['name']][1] += carry
        by_tool[r['name']][2] += raw
        items.append((carry, raw, r['remaining'] or 0, r['name'], r['session_id'], r['ts']))
    total = sum(v[1] for v in by_tool.values()) or 1
    print('# tokenwise carry cost -- %sZ -> %sZ' % (a[:16], b[:16]))
    print('total carry ~%s tokens of cache-read caused by tool results being re-sent on later turns\n' % _fmt(total))
    print('%-22s %6s %8s %9s  share' % ('tool', 'calls', 'raw', 'carry'))
    for name, (n, carry, raw) in sorted(by_tool.items(), key=lambda kv: -kv[1][1])[:10]:
        print('%-22s %6d %8s %9s  %4.0f%%' % (name[:22], n, _fmt(raw), _fmt(carry), 100 * carry / total))
    print('\ntop single results by carry (raw x turns that re-sent it):')
    for carry, raw, rem, name, sid, ts in sorted(items, reverse=True)[:12]:
        print('  %8s = %7s x %4d turns  %-14s %s S%s' % (_fmt(carry), _fmt(raw), rem, name[:14], ts[:16], sid[:8]))
    early = sum(x[0] for x in items if x[2] >= 100)
    print('\n%.0f%% of all carry comes from results that landed with 100+ turns still to run.' % (100 * early / total))
    print('Levers, in order of structural effect: read it inside a subagent (its context dies, only the summary')
    print('returns) - reset at task boundaries (/clear, /compact) - cap the result at the source - fewer turns.')


def replay(args):
    """Counterfactual: run the task_boundary detector over prompts that ALREADY happened, and price what splitting
    the session at each firing would have saved.

    This is how the handler is judged without waiting a week: the decisions are replayed against real history using
    the very same classifier the live hook uses (imported, not reimplemented, so the two cannot drift).

    Saving model. Splits COMPOUND, so they are simulated in sequence over the whole session rather than priced one
    at a time: after a split at turn k the running context becomes (floor + reestablish), and every later turn
    carries only what accumulated since k. Formally the simulation tracks an offset,
        simulated_t = max(floor + reestablish, actual_t - offset),  offset := actual_k - (floor + reestablish),
    and the saving is the difference between the two series. The clamp matters: it stops the model claiming a
    saving the platform had already made by auto-compacting.

    Pricing 17 firings independently, as an earlier version of this function did, double-counts every later split
    and produced a nonsensical 271% of actual.
    """
    import sqlite3 as _sq
    sys.path.insert(0, os.path.join(ROOT, 'hooks'))
    import task_boundary as TB

    c = con()
    if not args.no_ingest:
        ingest()
    a, b = _window(args)
    reest = args.reestablish

    rc = _sq.connect(os.path.expanduser('~/.claude-tools/recall/recall.db'))
    rc.row_factory = _sq.Row

    turns = c.execute('SELECT session_id, ts, ctx FROM turns WHERE ts BETWEEN ? AND ? AND is_subagent=0 '
                      'ORDER BY session_id, ts', (a, b)).fetchall()
    by_sess = defaultdict(list)
    for r in turns:
        by_sess[r['session_id']].append((r['ts'], r['ctx'] or 0))
    floors = [v[0][1] for v in by_sess.values() if v and v[0][1] > 0]
    floor = statistics.median(floors) if floors else 32_000

    total_actual = sum(x[1] for v in by_sess.values() for x in v)
    fires, saved_total, touched = [], 0, 0
    for sid, series in by_sess.items():
        if len(series) < TB.MIN_TURNS:
            continue
        prompts = rc.execute("SELECT ts, text FROM chunks WHERE session_id=? AND role='user' ORDER BY ts",
                             (sid,)).fetchall()
        if not prompts:
            prompts = c.execute('SELECT ts, head AS text FROM prompts WHERE session_id=? ORDER BY ts', (sid,)).fetchall()
        if not prompts:
            continue
        touched += 1
        # 1. where would the live hook have fired?
        recent, last_fire_turn, splits = [], -10 ** 9, []
        ts_list = [t for t, _ in series]
        for p in prompts:
            cur = TB.terms(p['text'])
            if not cur:
                continue
            if recent:
                best = max(TB.overlap(cur, q) for q in recent)
                k = sum(1 for t in ts_list if t <= p['ts'])          # turns elapsed at this prompt
                if (best <= TB.MAX_OVERLAP and k >= TB.MIN_TURNS
                        and k - last_fire_turn >= TB.COOLDOWN_TURNS and k < len(series)):
                    last_fire_turn = k
                    splits.append(k)
            recent = (recent + [cur])[-TB.RECENT_PROMPTS:]

        # 2. simulate them IN SEQUENCE -- each split resets the accumulator for everything after it
        base = floor + reest
        offset, nxt, sess_saved = 0, 0, 0
        per_split = {k: 0 for k in splits}
        active = None
        for i, (_, actual) in enumerate(series, start=1):
            while nxt < len(splits) and splits[nxt] == i:
                offset = max(offset, actual - base)
                active = splits[nxt]
                nxt += 1
            sim = max(base, actual - offset)
            gain = max(0, actual - sim)
            sess_saved += gain
            if active is not None:
                per_split[active] += gain
        for k in splits:
            if per_split[k] > 0:
                fires.append((per_split[k], sid, k, len(series), series[k - 1][1], len(series) - k, 0.0))
        saved_total += sess_saved
    rc.close()

    print('# tokenwise replay -- task_boundary against history, %sZ -> %sZ' % (a[:16], b[:16]))
    print('classifier: >=%d turns and <=%.0f%% word overlap with the last %d prompts, %d-turn cooldown'
          % (TB.MIN_TURNS, 100 * TB.MAX_OVERLAP, TB.RECENT_PROMPTS, TB.COOLDOWN_TURNS))
    print('assumed floor %s + %s re-establish per split\n' % (_fmt(floor), _fmt(reest)))
    print('sessions long enough to qualify: %d   firings: %d' % (touched, len(fires)))
    if not fires:
        print('nothing would have fired in this window.')
        return
    print('actual cache-read in window: %s' % _fmt(total_actual))
    print('estimated avoidable:         %s  (%.0f%%)\n' % (_fmt(saved_total), 100 * saved_total / max(total_actual, 1)))
    print('%-10s %6s %6s %9s %8s %8s' % ('session', 'at', 'of', 'ctx then', 'after', 'saved'))
    for saved, sid, k, n, ctx_k, rem, ov in sorted(fires, reverse=True)[:12]:
        print('%-10s %6d %6d %9s %8d %8s' % ('S' + sid[:8], k, n, _fmt(ctx_k), rem, _fmt(saved)))
    print('\nRead as: had you cleared at these points, that much cache-read would not have been re-sent.')
    print('It is an estimate, not a measurement -- the real number arrives once the live hook has a week of use.')


def actions(args):
    c = con()
    since = (dt.datetime.now() - dt.timedelta(days=args.days or 7)).strftime('%Y-%m-%dT%H:%M:%S')
    for r in c.execute('SELECT ts, session_id, handler, detail, est_saved FROM actions WHERE ts>=? ORDER BY ts DESC LIMIT 200', (since,)):
        print(f"{r['ts'][:16]} {r['session_id'][:8]} {r['handler']:22s} {r['detail'][:110]} {('~' + _fmt(r['est_saved'])) if r['est_saved'] else ''}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('ingest'); s.add_argument('-v', action='store_true')
    s = sp.add_parser('report'); s.add_argument('--today', action='store_true'); s.add_argument('--week', action='store_true')
    s.add_argument('--days', type=int); s.add_argument('--session'); s.add_argument('--no-ingest', action='store_true')
    s = sp.add_parser('actions'); s.add_argument('--days', type=int)
    s = sp.add_parser('compare'); s.add_argument('--split', required=True, help="local 'YYYY-MM-DD HH:MM'")
    s.add_argument('--days', type=int); s.add_argument('--no-ingest', action='store_true')
    s = sp.add_parser('waste'); s.add_argument('--days', type=int); s.add_argument('--today', action='store_true')
    s.add_argument('--session'); s.add_argument('--no-ingest', action='store_true')
    s = sp.add_parser('replay'); s.add_argument('--days', type=int); s.add_argument('--today', action='store_true')
    s.add_argument('--session'); s.add_argument('--no-ingest', action='store_true')
    s.add_argument('--reestablish', type=int, default=40000)
    a = p.parse_args()
    if a.cmd == 'ingest':
        print(ingest(a.v))
    elif a.cmd == 'report':
        report(a)
    elif a.cmd == 'compare':
        compare(a)
    elif a.cmd == 'waste':
        waste(a)
    elif a.cmd == 'replay':
        replay(a)
    else:
        actions(a)


if __name__ == '__main__':
    main()
