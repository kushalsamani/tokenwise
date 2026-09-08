#!/usr/bin/env python3
"""PreToolUse(Agent): a subagent spawned without an explicit model runs on sonnet. Subagent transcripts carry their
own usage, so the saving is measured, not assumed. `fork` ignores model and is left alone; an explicit model is
always respected."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

DEFAULT = os.environ.get('TOKENWISE_SUBAGENT_MODEL', 'sonnet')


def main():
    if C.disabled():
        return
    d = C.read_stdin()
    if d.get('tool_name') != 'Agent':
        return
    inp = d.get('tool_input') or {}
    if inp.get('model') or inp.get('subagent_type') == 'fork':
        return
    new_input = dict(inp)
    new_input['model'] = DEFAULT
    C.log_action(d.get('session_id'), 'agent_model_default', f"{inp.get('subagent_type', '')}|{inp.get('description', '')}|{DEFAULT}")
    C.respond('PreToolUse', updated_input=new_input,
              additional_context=f'tokenwise: subagent will run on {DEFAULT} (default for unspecified model). '
                                 f'Pass model explicitly when the task needs the main model.')


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:
        C.debug(f'agent_model_default error {ex}')
    sys.exit(0)
