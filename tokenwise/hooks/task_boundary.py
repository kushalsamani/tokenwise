#!/usr/bin/env python3
"""UserPromptSubmit: notice when a NEW task starts inside an already-long session, and say what carrying the old
context will cost from here.

WHY THIS ONE. Measured on this machine over 7 days: tool results caused ~324M tokens of cache-read carry, 87% of it
from Bash -- yet the worst single results were only 2-8K tokens each. They were expensive because they were re-sent
1,100-1,400 times. 98% of all carry came from results that landed with 100+ turns still to run.

So the multiplier is TURNS REMAINING, not result size. Capping outputs is second-order; not carrying an old task's
context through a new one is first-order. This handler fires only where both conditions hold: the session is already
long, and the new prompt has little in common with what came before. It never blocks and never edits the prompt.

THE SAFETY INTERLOCK. Telling someone to clear is only honest if the finished work survives the clear. Two stores
are understood, in order: a knowledge store named by TOKENWISE_STORE_DB / TOKENWISE_STORE_CLI, and failing that the
memory files Claude Code already keeps for the project, which a fresh session loads by itself. With neither, the
handler still fires and says plainly that it could verify nothing.
"""
import datetime
import json
import os
import re
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _common as C  # noqa: E402

MIN_TURNS = int(C.setting('TOKENWISE_BOUNDARY_MIN_TURNS', '120'))
MAX_OVERLAP = float(C.setting('TOKENWISE_BOUNDARY_MAX_OVERLAP', '0.12'))
RECENT_PROMPTS = 4
COOLDOWN_TURNS = int(C.setting('TOKENWISE_BOUNDARY_COOLDOWN', '60'))   # do not nag twice inside this many turns
STOP = set('''the a an and or but for with from this that these those into over under when what where which while
about after before then than they them their there here have has had been being was were will would can could should
you your yours our ours it its his her not now just also more most some any all one two how why who whom whose do does
did done make made get got let need want use used using please okay yes no ok'''.split())


def terms(text):
    return {w for w in re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{2,}', (text or '').lower()) if w not in STOP}


def overlap(a, b):
    if not a or not b:
        return 1.0                      # unknown -> behave as "related", stay silent
    return len(a & b) / len(a | b)


def session_turns(transcript_path):
    """Main-thread API turns so far, from the ledger (incrementally refreshed for just this transcript)."""
    try:
        import ledger
        c = ledger.con()
        ledger.ingest_file(c, transcript_path)
        sid = os.path.basename(transcript_path)[:-6]
        n = c.execute('SELECT COUNT(*) FROM turns WHERE session_id=? AND is_subagent=0', (sid,)).fetchone()[0]
        c.close()
        return int(n or 0)
    except Exception as ex:
        C.debug(f'task_boundary: turn count failed {ex}')
        return 0


# --------------------------------------------------------------------------- store 1: a configured knowledge store
def store_db():
    return C.setting('TOKENWISE_STORE_DB', '')


def state_captured(cwd, hours=12):
    """Is the current project's recent work already in the configured store? Returns (bool, summary)."""
    db = store_db()
    if not db or not os.path.exists(db):
        return None, 'no knowledge store configured'      # None = unknown, not "unsafe"
    try:
        con = sqlite3.connect(db, timeout=5)
        con.row_factory = sqlite3.Row
        since = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
        wi = con.execute('SELECT COUNT(*) FROM work_items WHERE ts>=?', (since,)).fetchone()[0]
        op = con.execute('SELECT COUNT(*) FROM open_items WHERE ts>=? AND closed_at IS NULL', (since,)).fetchone()[0]
        de = con.execute('SELECT COUNT(*) FROM decisions WHERE ts>=?', (since,)).fetchone()[0]
        con.close()
    except Exception as ex:
        return False, f'could not read the store ({ex})'
    if wi or op or de:
        return True, f'{wi} work item(s), {de} decision(s), {op} open item(s) recorded in the last {hours}h'
    return False, f'nothing recorded in the last {hours}h'


def continuity(cwd):
    """What a FRESH session would start with, so the recommendation can be honest about output quality."""
    try:
        cli = C.setting('TOKENWISE_STORE_CLI', '')
        if not cli or not os.path.exists(cli):
            return 0, {}, ''
        r = subprocess.run([sys.executable, cli, 'context', '--cwd', cwd],
                           capture_output=True, text=True, timeout=12)
        out = r.stdout.strip()
    except Exception:
        return 0, {}, ''
    if not out:
        return 0, {}, ''
    counts = {'workstreams': 0, 'open items': 0, 'decisions': 0, 'feedback': 0}
    section = None
    for line in out.splitlines():
        low = line.lower()
        if low.startswith('open items'):
            section = 'open items'
        elif low.startswith('recent decisions'):
            section = 'decisions'
        elif low.startswith('unaddressed feedback'):
            section = 'feedback'
        elif line.startswith('- **'):
            counts['workstreams'] += 1
        elif line.startswith('- ') and section:
            counts[section] += 1
    return len(out.splitlines()), counts, out


# --------------------------------------------------------------------------- store 2: Claude Code's own memory files
PROJECTS = os.path.expanduser('~/.claude/projects')


def project_dir(cwd):
    """The ~/.claude/projects directory Claude Code keeps for this working directory.

    Claude Code names it after the path with EACH non-alphanumeric character replaced by a dash, runs and all:
    `C:\\Users\\me\\My Repo` becomes `C--Users-me-My-Repo`, because the colon and the separator are two characters.
    Collapsing them would produce `C-Users-...` and match nothing. The drive letter's case varies with how the
    directory was entered, so the match is case-insensitive.
    """
    slug = re.sub(r'[^A-Za-z0-9]', '-', cwd or '')
    if not slug:
        return None
    try:
        for name in os.listdir(PROJECTS):
            if name.lower() == slug.lower():
                return os.path.join(PROJECTS, name)
    except OSError:
        pass
    return None


def memory_state(cwd, hours=12):
    """The zero-configuration store: the memory notes Claude Code already writes for this project, which a new
    session loads on its own. Returns (captured, detail, counts) with the same meaning as state_captured."""
    d = project_dir(cwd)
    mem = os.path.join(d, 'memory') if d else None
    if not mem or not os.path.isdir(mem):
        return None, 'no memory notes for this project yet', {}
    try:
        names = [f for f in os.listdir(mem) if f.endswith('.md')]
    except OSError:
        return None, 'memory notes unreadable', {}
    notes = [f for f in names if f.lower() != 'memory.md']
    index = 0
    idx_path = os.path.join(mem, 'MEMORY.md')
    if os.path.exists(idx_path):
        try:
            with open(idx_path, encoding='utf-8', errors='replace') as fh:
                index = sum(1 for line in fh if line.startswith('- ['))
        except OSError:
            pass
    counts = {'memory notes': index or len(notes)}
    cutoff = datetime.datetime.now().timestamp() - hours * 3600
    fresh = 0
    for f in names:
        try:
            if os.path.getmtime(os.path.join(mem, f)) >= cutoff:
                fresh += 1
        except OSError:
            continue
    if not notes:
        return False, f'the memory index exists but holds no notes yet', counts
    if fresh:
        return True, f'{fresh} memory note(s) written in the last {hours}h, {index or len(notes)} in the index', counts
    return False, f'{index or len(notes)} memory note(s) on file, none updated in the last {hours}h', counts


def durable(cwd):
    """(captured, detail, counts): whichever store this machine actually has."""
    if store_db():
        captured, detail = state_captured(cwd)
        _, counts, _ = continuity(cwd)
        return captured, detail, counts
    return memory_state(cwd)


def main():
    if C.disabled():
        return
    d = C.read_stdin()
    prompt = d.get('prompt') or ''
    sid = d.get('session_id') or ''
    path = d.get('transcript_path')
    if d.get('source') == 'slash_command' or prompt.lstrip().startswith('/') or len(prompt.split()) < 4:
        return
    if not path or not os.path.exists(path):
        return

    cur = terms(prompt)
    prev_raw = C.meta_get(f'bprompts:{sid}', '')
    prev_sets = [set(x.split(',')) - {''} for x in prev_raw.split('|')] if prev_raw else []
    # remember this prompt for next time (bounded)
    keep = (prev_sets + [cur])[-RECENT_PROMPTS:]
    C.meta_set(f'bprompts:{sid}', '|'.join(','.join(sorted(s)[:60]) for s in keep))
    if not prev_sets:
        return

    best = max(overlap(cur, p) for p in prev_sets)
    if best > MAX_OVERLAP:
        return                                   # continuation of the same work: carrying context is correct

    turns = session_turns(path)
    if turns < MIN_TURNS:
        return
    last_fire = int(C.meta_get(f'bfired:{sid}', 0) or 0)
    if turns - last_fire < COOLDOWN_TURNS:
        return
    C.meta_set(f'bfired:{sid}', turns)
    cwd = d.get('cwd') or os.getcwd()
    captured, detail, counts = durable(cwd)
    C.log_action(sid, 'task_boundary', f'turns={turns}|overlap={best:.2f}|captured={captured}|'
                                       f'{re.sub(chr(32) + "+", " ", prompt.strip())[:80]}')
    carry = ', '.join(f'{v} {k}' for k, v in counts.items() if v) or 'nothing yet'
    if captured is None:
        C.notify('New topic detected',
                 f'{turns} turns carried into an unrelated task. Consider /clear once anything unsaved is written down.')
        action = ('Nothing durable could be verified automatically, so do not claim it was. Say in one visible '
                  'line at the START of your reply: "New topic, carrying N turns of unrelated context; if the '
                  'previous work is written down somewhere, /clear is worth it." Then answer the prompt.')
    elif captured:
        C.notify('New topic, safe to clear',
                 f'{turns} turns carried. A new chat opens with {carry}. Output quality unchanged, tokens much lower.')
        action = (f'The previous work IS durable ({detail}), and a fresh session would open with {carry}, so output '
                  f'quality does not degrade, only the token cost drops. A desktop notification has already been '
                  f'shown. Still say, in one visible line at the START of your reply: "New topic, previous work is '
                  f'saved ({detail}); a new chat would open with {carry}, so answers stay as good. Recommend '
                  f'/clear." Then answer the prompt.')
    else:
        C.notify('New topic, save first',
                 f'{turns} turns carried, but this work is not written down yet. Claude is recording it now.')
        action = (f'The previous work is NOT durable ({detail}), so clearing WOULD lose it. Before anything else, '
                  f'write the finished work down where a new session will find it: a memory note for this project, '
                  f'or whatever store this repo uses. A desktop notification has told the user this is happening. '
                  f'Then say in one visible line at the START of your reply: "Saved the previous task as <refs>; a '
                  f'new chat would open with that plus {carry}, so answers stay as good. Recommend /clear." Then '
                  f'answer the prompt.')
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': (
            f'tokenwise: new task detected (overlap {best:.0%} with the last {len(prev_sets)} prompts) in a session '
            f'already {turns} turns long. Carrying this context costs its full size on every remaining turn. '
            f'{action} If the new prompt actually depends on the earlier work, skip all of this silently and just '
            f'answer, do not nag.')}}))


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:  # a hook bug must never cost the user a turn
        C.debug(f'task_boundary error {ex}')
    sys.exit(0)
