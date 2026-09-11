#!/usr/bin/env python3
"""PostToolUse(*) + Stop: watch the live context size and say so once per threshold.

Why this is the lever: every API turn re-sends the whole context. Measured on this machine, 7 days = 5,435 turns
re-reading 2.8 BILLION cached tokens at an average of ~517K per turn, while output was 5.4M. Nothing else comes
close. The governor cannot compact for you; it tells the model the number and what to do at the next natural
boundary (checkpoint, then /clear or /compact)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

THRESHOLDS = [int(x) for x in C.setting('TOKENWISE_CTX_THRESHOLDS', '150000,300000,450000').split(',')]
TAIL_BYTES = 400_000


def last_context(transcript_path):
    """Context size of the most recent assistant turn = input + cache_creation + cache_read."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, 'rb') as fh:
            fh.seek(max(0, size - TAIL_BYTES))
            tail = fh.read().decode('utf-8', 'replace')
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if '"type":"assistant"' not in line and '"type": "assistant"' not in line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        u = ((e.get('message') or {}).get('usage')) or {}
        if not u:
            continue
        return (u.get('input_tokens', 0) or 0) + (u.get('cache_creation_input_tokens', 0) or 0) + (u.get('cache_read_input_tokens', 0) or 0)
    return None


def main():
    if C.disabled():
        return
    d = C.read_stdin()
    event = d.get('hook_event_name') or 'PostToolUse'
    sid = d.get('session_id') or ''
    path = d.get('transcript_path')
    if not path or not os.path.exists(path):
        return
    ctx = last_context(path)
    if ctx is None:
        return
    state = (C.meta_get(f'gov:{sid}', '0|0') or '0|0').split('|')
    fired, ctx_at_fire = int(state[0] or 0), int(state[1] or 0)
    if fired and ctx < 0.6 * ctx_at_fire:      # context dropped (compact/clear happened): re-arm every threshold
        fired = 0
        C.meta_set(f'gov:{sid}', '0|0')
    due = [t for t in THRESHOLDS if ctx >= t and t > fired]
    if not due:
        return
    t = max(due)
    C.meta_set(f'gov:{sid}', f'{t}|{ctx}')
    C.log_action(sid, 'context_governor', f'ctx={ctx}|threshold={t}|event={event}')
    msg = (f'tokenwise: context is ~{ctx // 1000}K tokens, re-sent on every remaining turn. At this size each further '
           f'turn costs ~{ctx // 1000}K cache-read, and anything you add now costs its own size times the turns left '
           f'(measured: 98% of this machine\'s carry cost came from results added with 100+ turns still to run). '
           f'Cheapest moves from here, in order: push exploratory reading into a subagent so its context is discarded; '
           f'finish the step, checkpoint what is verified and open, then /clear for a new task or /compact to continue '
           f'this one. Do not stop mid-edit for this.')
    C.respond(event, additional_context=msg)


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:
        C.debug(f'context_governor error {ex}')
    sys.exit(0)
