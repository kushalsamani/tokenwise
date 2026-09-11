#!/usr/bin/env python3
"""PreToolUse(Read): a whole-file Read of a long text file is rewritten to the first CAP lines, with a note saying how
long the file is so the model asks for ranges. Reads that already pass offset/limit are untouched. Binary, image,
PDF and notebook files are untouched. Follow-up ranged reads of a capped file are logged so the net effect
(tokens withheld vs extra turns) is measurable."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

CAP = int(C.setting('TOKENWISE_READ_CAP', '600'))
SKIP_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf', '.ipynb', '.svg', '.ico', '.mov', '.mp4', '.zip', '.gz', '.jar', '.class'}


def count_lines(path, max_bytes=50_000_000):
    n, size, chars = 0, 0, 0
    with open(path, 'rb') as fh:
        head = fh.read(8192)
        if b'\x00' in head:
            return None, 0
        fh.seek(0)
        for line in fh:
            n += 1
            chars += len(line)
            size += len(line)
            if size > max_bytes:
                break
    return n, chars


def main():
    if C.disabled():
        return
    d = C.read_stdin()
    if d.get('tool_name') != 'Read':
        return
    inp = d.get('tool_input') or {}
    path = inp.get('file_path') or ''
    sid = d.get('session_id')
    if not path or not os.path.isfile(path):
        return
    if os.path.splitext(path)[1].lower() in SKIP_EXT:
        return
    capped_before = C.meta_get(f'readcap:{sid}:{path}')
    if inp.get('offset') is not None or inp.get('limit') is not None:
        if capped_before:
            C.log_action(sid, 'read_guard.followup', path)
        return
    lines, chars = count_lines(path)
    if lines is None or lines <= CAP:
        return
    avg = chars / max(lines, 1)
    withheld_tokens = int((lines - CAP) * avg / 4)
    new_input = dict(inp)
    new_input['limit'] = CAP
    C.meta_set(f'readcap:{sid}:{path}', lines)
    C.log_action(sid, 'read_guard.cap', f'{path}|lines={lines}|cap={CAP}', withheld_tokens)
    C.respond('PreToolUse', updated_input=new_input,
              additional_context=f'tokenwise: {os.path.basename(path)} has {lines} lines; you received lines 1-{CAP}. '
                                 f'Read further ranges with offset/limit only where needed (or grep for the symbol first).')


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:
        C.debug(f'read_guard error {ex}')
    sys.exit(0)
