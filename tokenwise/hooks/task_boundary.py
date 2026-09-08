#!/usr/bin/env python3
"""UserPromptSubmit: notice when a NEW task starts inside an already-long session, and say what carrying the old
context will cost from here.

WHY THIS ONE. Measured on this machine over 7 days: tool results caused ~324M tokens of cache-read carry, 87% of it
from Bash — yet the worst single results were only 2-8K tokens each. They were expensive because they were re-sent
1,100-1,400 times. 98% of all carry came from results that landed with 100+ turns still to run.

So the multiplier is TURNS REMAINING, not result size. Capping outputs is second-order; not carrying an old task's
context through a new one is first-order. This handler fires only where both conditions hold: the session is already
long, and the new prompt has little in common with what came before. It never blocks and never edits the prompt.
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

MIN_TURNS = int(os.environ.get('TOKENWISE_BOUNDARY_MIN_TURNS', '120'))
MAX_OVERLAP = float(os.environ.get('TOKENWISE_BOUNDARY_MAX_OVERLAP', '0.12'))
RECENT_PROMPTS = 4
COOLDOWN_TURNS = 60          # do not nag twice inside this many turns
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


def state_captured(cwd, hours=12):
    """Is the current project's recent work already in the recall store? Returns (bool, summary).

    This is the safety interlock: clearing is only safe if what is being discarded already exists somewhere
    durable. If it does not, the model is told to write it BEFORE recommending a clear.
    """
    db = os.environ.get('TOKENWISE_STORE_DB', os.path.expanduser('~/.claude-tools/recall/recall.db'))
    if not os.path.exists(db):
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
    """What a FRESH session would start with, so the recommendation can be honest about output quality.

    Clearing is only worth recommending if the new session is not born ignorant. The recall SessionStart hook
    injects an opener built from the store; this runs the same command and reports what it would contain, so the
    user is told concretely what carries over rather than being asked to trust it.
    """
    try:
        cli = os.environ.get('TOKENWISE_STORE_CLI', os.path.expanduser('~/.claude-tools/recall/recall.py'))
        if not os.path.exists(cli):
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


def banner(title, message):
    """macOS notification. Deliberately NOT clickable and not a modal: there is nothing to open, and a dialog would
    block the hook until dismissed. Uses osascript directly so tokenwise stays standalone (no app bundle needed)."""
    try:
        msg = message.replace('"', "'")[:230]
        ttl = title.replace('"', "'")[:60]
        subprocess.run(['osascript', '-e',
                        f'display notification "{msg}" with title "{ttl}" sound name "Ping"'],
                       capture_output=True, timeout=8)
    except Exception:
        pass


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
    captured, detail = state_captured(d.get('cwd') or os.getcwd())
    C.log_action(sid, 'task_boundary', f'turns={turns}|overlap={best:.2f}|captured={captured}|'
                                       f'{re.sub(chr(32) + "+", " ", prompt.strip())[:80]}')
    cwd = d.get('cwd') or os.getcwd()
    nlines, counts, _ = continuity(cwd)
    carry = ', '.join(f'{v} {k}' for k, v in counts.items() if v) or 'nothing yet'
    if captured is None:
        banner('New topic detected',
               f'{turns} turns carried into an unrelated task. Consider /clear once anything unsaved is written down.')
        action = ('No knowledge store is configured, so nothing can be verified automatically. Say in one visible '
                  'line at the START of your reply: "New topic - carrying N turns of unrelated context; if the '
                  'previous work is written down somewhere, /clear is worth it." Then answer the prompt.')
    elif captured:
        banner('New topic - safe to clear',
               f'{turns} turns carried. A new chat opens with {carry}. Output quality unchanged; tokens much lower.')
        action = (f'The previous work IS durable in the store ({detail}), and a fresh session would open with '
                  f'{carry} ({nlines}-line opener), so output quality does not degrade - only the token cost drops. '
                  f'A desktop banner has already been shown to the user. Still say, in one visible line at the START '
                  f'of your reply: "New topic - previous work is saved ({detail}); a new chat would open with '
                  f'{carry}, so answers stay as good. Recommend /clear." Then answer the prompt.')
    else:
        banner('New topic - save first',
               f'{turns} turns carried, but this work is not in the store yet. Claude is writing a handoff now.')
        action = (f'The previous work is NOT in the store ({detail}), so clearing WOULD lose it. Before anything else '
                  f'record the finished work in your knowledge store '
                  f'--note ...` for the work that just finished. A desktop banner has told the user this is happening. '
                  f'Then say in one visible line at the START of your reply: "Saved the previous task as <refs>; a new '
                  f'chat would open with that plus {carry}, so answers stay as good. Recommend /clear." Then answer.')
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': (
            f'tokenwise: new task detected (overlap {best:.0%} with the last {len(prev_sets)} prompts) in a session '
            f'already {turns} turns long. Carrying this context costs its full size on every remaining turn. '
            f'{action} If the new prompt actually depends on the earlier work, skip all of this silently and just '
            f'answer - do not nag.')}}))


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:
        C.debug(f'task_boundary error {ex}')
    sys.exit(0)
