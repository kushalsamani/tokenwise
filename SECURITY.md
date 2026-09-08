# Security

## The thing you should worry about

Claude Code hooks are **remote code execution by design**. Once wired into `settings.json` they run automatically,
with your user's privileges, on every prompt and every tool call, with no further consent. That is fine for code you
wrote. It is not fine for code that arrived in a pull request from a stranger.

So the dangerous moment for this project is not "does the tool work" — it is **"someone merged a PR and you ran
`git pull && ./install.sh`"**. Everything below exists for that moment.

## What a hook here may never do

These are enforced by `audit.py`, which the installer runs **before** anything is wired, and CI runs on every pull
request. A finding blocks the install; there is no `--force`.

| Forbidden | Why |
|---|---|
| `permissionDecision` of any kind | A hook returning `allow` would auto-approve tool calls you never saw. This is the single most dangerous thing a hook could do, so the string is banned outright. |
| Network modules — `socket`, `urllib`, `requests`, `http`, `smtplib`, … | A hook sees every prompt you type. It must not be able to send them anywhere. |
| `eval`, `exec`, `compile`, `__import__`, `pickle`, `marshal` | Dynamic execution and deserialisation are how a small diff hides a large payload. |
| `os.system`, `os.popen`, `subprocess.Popen`, `shell=True` | Shell strings are unreviewable. |
| `subprocess.run` with anything but a literal argv whose program is on a short allowlist | So a reviewer can see exactly what may launch. |
| `shutil.rmtree`, `os.remove`, `os.chmod`, … | Hooks observe and advise; they do not modify your filesystem. |
| Any mention of `settings.json` | Only `tools/wire.py` may touch your configuration, and it never runs automatically. |
| Reading `~/.ssh`, `.aws/credentials`, `/etc/passwd`, the macOS keychain | Obvious credential theft. |
| Files over 60 KB | A hook that needs more than that is not a hook. |

If a contribution genuinely needs an exception, it has to edit `audit.py` **in the same pull request**, where a
reviewer sees the policy change sitting next to the code that wants it. That is the point: the gate cannot be
widened silently.

## Layers

1. **Static audit** (`audit.py`) — AST-based, not regex-only, so `getattr(os, "sys" + "tem")` style tricks fail the
   subprocess and call rules. Runs before install, and in CI.
2. **Manifest verification** (`MANIFEST.sha256`) — every file the installer wires or executes is hashed. If a file
   on disk differs from the manifest, install stops. So what was audited and reviewed is what actually gets wired.
   Override with `--allow-local-changes` only for your own edits, never for a fresh clone.
3. **No privileged install** — the installer refuses to run as root, touches nothing outside `~/.claude` and the
   repo, makes no network calls, and takes a timestamped backup of `settings.json` before editing it.
4. **Idempotent, reversible wiring** — re-running replaces the previous tokenwise entries instead of stacking them,
   leaves other tools' hooks untouched, re-parses the file afterwards, and `uninstall.sh` removes them cleanly.
5. **No auto-update** — nothing pulls, checks a remote, or self-updates. New code arrives only when you run
   `git pull` yourself, and the audit re-runs on the next install.
6. **Least capability at runtime** — every handler only ever returns `additionalContext` (advisory text) or, in two
   narrow cases, `updatedInput`. None blocks a tool call.

## Before you `git pull` someone else's change

```bash
git log --oneline HEAD..@{u}          # what is arriving
git diff HEAD..@{u} -- tokenwise/ audit.py tools/    # read the code that will run automatically
python3 audit.py --manifest           # audit the incoming tree
./install.sh --dry-run                # see exactly what would be wired
```

Pay closest attention to any diff that touches `audit.py`, `tools/wire.py`, or `install.sh`. Those three files are
the security boundary; everything else is a hook the audit already constrains.

## For maintainers

- Require review on `main`; do not allow direct pushes.
- `CODEOWNERS` covers `audit.py`, `tools/`, `install.sh`, `uninstall.sh` and `.github/`. Changes there need the
  owner's approval.
- CI runs `audit.py` and the test suite on every pull request. Do not merge on a red run.
- Regenerate `MANIFEST.sha256` (`python3 audit.py --write-manifest`) as part of the release commit, never blindly
  as part of merging someone else's branch.
- Treat a PR that adds a new external program to `ALLOWED_PROGRAMS`, or a new `subprocess` call, as a security
  review, not a feature review.

## Reporting

Open a private security advisory on the repository rather than a public issue. If you found a way to make a hook do
something on this list without `audit.py` catching it, that is the bug worth reporting — the audit is the product.

## Honest limits

`audit.py` is a static check, not a sandbox. A sufficiently clever contributor can defeat any static analysis, and
Python gives them plenty of room. It raises the cost of hiding something and makes the dangerous shapes visible in
review; it does not make review optional. **Read the diff.**
