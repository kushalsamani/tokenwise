"""Behaviour tests for every hook. No network, no Claude calls: hooks are pure functions of stdin JSON."""
import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, 'tokenwise', 'hooks')


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


if __name__ == '__main__':
    unittest.main()
