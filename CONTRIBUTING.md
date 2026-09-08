# Contributing

Thanks for looking. One thing to understand before you start: **hooks in this repo run automatically on every
prompt and every tool call, on someone else's machine, with their privileges.** The review bar reflects that.

## Before you open a PR

```bash
python3 audit.py --path .                 # must pass; it is the same gate CI runs
python3 -m unittest discover -s tests     # must pass
./install.sh --dry-run                    # shows what your change would wire
```

## Rules for anything under `tokenwise/hooks/`

- Advisory only. Return `additionalContext`, or `updatedInput` where a narrow, documented rewrite is justified.
  Never a permission decision. Never block a tool call.
- Fast and offline. No network, no LLM call, no unbounded file walk. A hook runs on every prompt.
- Fail silently. Wrap `main()` so any exception exits 0 — a hook bug must never cost the user a turn.
- Deterministic and testable from stdin JSON, with a test in `tests/`.
- Add a docstring saying **what it intercepts, what it does instead, and what the measured justification is.**
  "It seemed like it would help" is not a justification; this project deleted its own read-cap claim on measurement.

## Measurement is part of the change

A handler that cannot be shown to help gets labelled low-value or removed. If you add one, say how it should be
measured — ideally add a task to `harness/tasks.example.jsonl` and report a before/after from
`ledger.py compare` or `ledger.py replay` on your own data.

## Things that need a security review, not a feature review

Any diff touching `audit.py`, `tools/`, `install.sh`, `uninstall.sh`, `.github/`, or adding a `subprocess` call or a
new entry to `ALLOWED_PROGRAMS`. Expect slow, sceptical review. See [SECURITY.md](SECURITY.md).

## Do not commit

Databases, `repos.txt`, `harness/tasks.jsonl`, `harness/results.tsv`, logs, or anything derived from your own
conversations or your employer's code. All of it is gitignored; check `git status` anyway.
