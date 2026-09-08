#!/usr/bin/env python3
"""UserPromptSubmit: classify the prompt by size of task and steer the model toward the cheapest adequate path.
Heuristic, advisory, logged. The model can always override; misroutes show up in the ledger (turns per prompt
by class) and the rules get pruned on evidence, not opinion.

Classes:
  TRIVIAL  short factual/status ask → answer in ≤2 tool calls, no subagents, no whole-file reads
  LOOKUP   "where is / what calls / which file" → use the where-is index before reading files
  MID      small bounded change → one Explore agent (sonnet, low effort) for gathering, ranged reads on main thread
  HEAVY    everything else → no steering
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

HEAVY_WORDS = re.compile(r'\b(all|every|entire|whole|across|refactor|architecture|audit|comprehensive|thorough|plan|design|'
                         r'migrate|rewrite|investigate|root[- ]cause|debug|why|explain|review|implement|build|create|new feature)\b', re.I)
TRIVIAL_START = re.compile(r'^\s*(what|which|where|who|when|is|are|does|did|do|can|show|list|print|run|check|git|status|'
                           r'open|cat|tail|head|how many|count|ls|pwd|which branch|whats|what\'s)\b', re.I)
CHANGE_WORDS = re.compile(r'\b(fix|add|change|update|rename|tweak|adjust|typo|bump|remove|delete|move|small|minor|quick|one[- ]line)\b', re.I)
LOOKUP_WORDS = re.compile(r'\b(where (is|are|does)|which file|find the|locate|defined|definition of|who calls|usages? of|'
                          r'callers? of|declared)\b', re.I)
IDENT = re.compile(r'\b[A-Z][a-z]+[A-Z]\w+\b|\b\w+_\w+\b|\b\w+\(\)')

MSG = {
    'TRIVIAL': 'tokenwise route=TRIVIAL: answer with at most 2 tool calls, no subagents, no whole-file reads; if it turns out '
               'to need more, say so in one line and continue normally.',
    'LOOKUP': 'tokenwise route=LOOKUP: run `~/.claude-tools/tokenwise/whereis.py <Symbol>` (or /where-is) first; it returns '
              'file:line for definitions so you read a range, not files.',
    'MID': 'tokenwise route=MID: bounded change. Gather with ONE Explore agent (model sonnet, effort low) if any search is '
           'needed; on the main thread read only the ranges you will edit; batch independent shell commands in one call.',
    'HEAVY': 'tokenwise route=HEAVY: this will read a lot. Whatever enters the main thread is re-sent on every later turn, '
             'so do the exploring inside subagents (their context is discarded and only the summary returns) and keep the '
             'main thread for decisions and edits. Batch independent shell commands into single calls.',
}


def classify(prompt):
    words = len(prompt.split())
    if words > 120:
        return 'HEAVY'
    heavy = bool(HEAVY_WORDS.search(prompt))
    if words <= 40 and LOOKUP_WORDS.search(prompt) and not heavy:
        return 'LOOKUP'
    if words <= 40 and IDENT.search(prompt) and re.search(r'\bwhere\b|\bwhich\b|\bfind\b', prompt, re.I) and not heavy:
        return 'LOOKUP'
    if words <= 25 and (TRIVIAL_START.match(prompt) or prompt.strip().endswith('?')) and not heavy and not CHANGE_WORDS.search(prompt):
        return 'TRIVIAL'
    if words <= 60 and CHANGE_WORDS.search(prompt) and not heavy:
        return 'MID'
    return 'HEAVY'


def main():
    if C.disabled():
        return
    d = C.read_stdin()
    C.note_account(d.get('session_id'))     # cheap, once per session; see _common.note_account
    prompt = d.get('prompt') or ''
    if d.get('source') == 'slash_command' or prompt.lstrip().startswith('/') or not prompt.strip():
        return
    cls = classify(prompt)
    head = re.sub(r'\s+', ' ', prompt.strip())[:120]
    C.log_action(d.get('session_id'), 'router', f'{cls}|{head}')
    if cls in MSG:
        C.respond('UserPromptSubmit', additional_context=MSG[cls])


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:  # a hook bug must never cost the user a turn
        C.debug(f'router error {ex}')
    sys.exit(0)
