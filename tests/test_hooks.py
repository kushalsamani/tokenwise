"""Behaviour tests for every hook. No network, no Claude calls: hooks are pure functions of stdin JSON.

The last four classes cover what now varies by platform: where a setting comes from, what a wired hook command
looks like, whether the notifier can be silenced, and the zero-configuration memory store. They assert the shape
for the platform they are running on, so the same file passes on macOS, Linux and Windows.
"""
import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, 'tokenwise', 'hooks')
sys.path.insert(0, HOOKS)
sys.path.insert(0, os.path.join(ROOT, 'tools'))


def run(hook, payload, env=None):
    e = dict(os.environ)
    e['TOKENWISE_DB'] = os.path.join(tempfile.gettempdir(), 'tw-test.db')
    e.pop('TOKENWISE_OFF', None)
    e.update(env or {})
    p = subprocess.run([sys.executable, os.path.join(HOOKS, hook)], input=json.dumps(payload),
                       capture_output=True, text=True, env=e, timeout=30)
    assert p.returncode == 0, f'{hook} exited {p.returncode}: {p.stderr}'
    return json.loads(p.stdout) if p.stdout.strip() else None


class Router(unittest.TestCase):
    def test_trivial_prompt_is_steered(self):
        out = run('router.py', {'session_id': 't', 'prompt': 'what branch am I on?', 'source': 'user'})
        self.assertIn('TRIVIAL', out['hookSpecificOutput']['additionalContext'])

    def test_heavy_prompt_steers_to_subagents(self):
        out = run('router.py', {'session_id': 't', 'source': 'user',
                                'prompt': 'refactor the whole pipeline and audit every guardrail across the repo'})
        self.assertIn('subagent', out['hookSpecificOutput']['additionalContext'].lower())

    def test_slash_command_ignored(self):
        self.assertIsNone(run('router.py', {'session_id': 't', 'prompt': '/help', 'source': 'slash_command'}))


class ReadGuard(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False)
        self.f.write('line\n' * 5000); self.f.close()

    def tearDown(self):
        os.unlink(self.f.name)

    def test_caps_unranged_read_of_long_file(self):
        out = run('read_guard.py', {'session_id': 'r', 'tool_name': 'Read',
                                    'tool_input': {'file_path': self.f.name}}, {'TOKENWISE_READ_CAP': '600'})
        self.assertEqual(out['hookSpecificOutput']['updatedInput']['limit'], 600)

    def test_leaves_ranged_read_alone(self):
        self.assertIsNone(run('read_guard.py', {'session_id': 'r2', 'tool_name': 'Read',
                                                'tool_input': {'file_path': self.f.name, 'offset': 1, 'limit': 5}}))


class AgentModel(unittest.TestCase):
    def test_defaults_model(self):
        out = run('agent_model_default.py', {'session_id': 'a', 'tool_name': 'Agent',
                                             'tool_input': {'subagent_type': 'Explore', 'prompt': 'x'}})
        self.assertEqual(out['hookSpecificOutput']['updatedInput']['model'], 'sonnet')

    def test_respects_explicit_model_and_fork(self):
        self.assertIsNone(run('agent_model_default.py', {'session_id': 'a', 'tool_name': 'Agent',
                                                         'tool_input': {'subagent_type': 'fork', 'prompt': 'x'}}))
        self.assertIsNone(run('agent_model_default.py', {'session_id': 'a', 'tool_name': 'Agent',
                                                         'tool_input': {'model': 'opus', 'prompt': 'x'}}))


class KillSwitch(unittest.TestCase):
    def test_off_disables_everything(self):
        for h, p in (('router.py', {'session_id': 'k', 'prompt': 'what branch am I on?', 'source': 'user'}),
                     ('agent_model_default.py', {'session_id': 'k', 'tool_name': 'Agent',
                                                 'tool_input': {'subagent_type': 'Explore'}})):
            self.assertIsNone(run(h, p, {'TOKENWISE_OFF': '1'}), h)


class NeverDecidesPermissions(unittest.TestCase):
    """The rule SECURITY.md leads with, asserted at runtime as well as statically."""
    def test_no_hook_returns_a_permission_decision(self):
        cases = [('router.py', {'session_id': 'p', 'prompt': 'delete everything now', 'source': 'user'}),
                 ('read_guard.py', {'session_id': 'p', 'tool_name': 'Read', 'tool_input': {'file_path': __file__}}),
                 ('agent_model_default.py', {'session_id': 'p', 'tool_name': 'Agent',
                                             'tool_input': {'subagent_type': 'Explore'}})]
        for h, p in cases:
            out = run(h, p)
            if out:
                self.assertNotIn('permissionDecision', json.dumps(out), h)


class Settings(unittest.TestCase):
    """The environment wins, then config.local.json, then the built-in default."""

    def setUp(self):
        import _common as C
        self.C = C
        self.saved = C.CONFIG
        self.tmp = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
        self.tmp.write(json.dumps({'TOKENWISE_BOUNDARY_MIN_TURNS': '999'}))
        self.tmp.close()
        C.CONFIG = self.tmp.name
        C._cfg_cache = None
        os.environ.pop('TOKENWISE_BOUNDARY_MIN_TURNS', None)

    def tearDown(self):
        self.C.CONFIG = self.saved
        self.C._cfg_cache = None
        os.environ.pop('TOKENWISE_BOUNDARY_MIN_TURNS', None)
        os.unlink(self.tmp.name)

    def test_file_is_used_when_the_environment_is_silent(self):
        self.assertEqual(self.C.setting('TOKENWISE_BOUNDARY_MIN_TURNS', '120'), '999')

    def test_environment_wins(self):
        os.environ['TOKENWISE_BOUNDARY_MIN_TURNS'] = '7'
        self.assertEqual(self.C.setting('TOKENWISE_BOUNDARY_MIN_TURNS', '120'), '7')

    def test_default_when_neither_is_set(self):
        self.assertEqual(self.C.setting('TOKENWISE_NOT_A_REAL_KEY', 'fallback'), 'fallback')


class Notifier(unittest.TestCase):
    def test_setting_silences_it(self):
        import _common as C
        os.environ['TOKENWISE_NOTIFY'] = '0'
        try:
            C.notify('title', 'message')      # must not launch anything, must not raise
        finally:
            os.environ.pop('TOKENWISE_NOTIFY', None)


class Wiring(unittest.TestCase):
    """wire.py is the only thing allowed to write settings.json, so its output shape is worth pinning."""

    def test_command_shape_matches_the_platform(self):
        import wire
        built = wire.build(ROOT, 'PY', 'exec' if wire.WINDOWS else 'shell', [])
        hook = built['UserPromptSubmit'][0]['hooks'][0]
        if wire.WINDOWS:
            # exec form: no shell, so no quoting rules to get wrong and no /usr/bin/env to be missing
            self.assertEqual(hook['command'], 'PY')
            self.assertTrue(hook['args'][0].endswith('router.py'))
            self.assertNotIn('\\', hook['args'][0])
        else:
            self.assertTrue(hook['command'].startswith('/usr/bin/env python3 '))
            self.assertIn('router.py', hook['command'])

    def test_skip_removes_only_what_is_named(self):
        import wire
        form = 'exec' if wire.WINDOWS else 'shell'
        built = wire.build(ROOT, 'PY', form, ['router', 'context_governor:PostToolUse'])
        flat = json.dumps(built)
        self.assertNotIn('router.py', flat)
        self.assertIn('task_boundary.py', flat)
        self.assertNotIn('PostToolUse', built)
        self.assertIn('Stop', built)


class MemoryStore(unittest.TestCase):
    """The zero-configuration store: the memory notes Claude Code already keeps for a project."""

    def test_counts_notes_and_notices_recent_writes(self):
        import re
        import task_boundary as TB
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.path.join(tmp, 'Some Project')       # a space in the path, as real ones have
            os.makedirs(cwd)
            slug = re.sub(r'[^A-Za-z0-9]', '-', cwd)      # how Claude Code names the project directory
            mem = os.path.join(tmp, 'projects', slug, 'memory')
            os.makedirs(mem)
            with open(os.path.join(mem, 'MEMORY.md'), 'w', encoding='utf-8') as fh:
                fh.write('# Memory Index\n- [One](one.md) - a note\n- [Two](two.md) - another\n')
            for n in ('one.md', 'two.md'):
                with open(os.path.join(mem, n), 'w', encoding='utf-8') as fh:
                    fh.write('body\n')
            saved = TB.PROJECTS
            TB.PROJECTS = os.path.join(tmp, 'projects')
            try:
                captured, detail, counts = TB.memory_state(cwd)
                self.assertTrue(captured)                    # just written, so inside the window
                self.assertEqual(counts['memory notes'], 2)  # from the index, not the file count
                self.assertIn('index', detail)
                self.assertIsNone(TB.memory_state(os.path.join(tmp, 'Nothing Here'))[0])
            finally:
                TB.PROJECTS = saved


if __name__ == '__main__':
    unittest.main()
