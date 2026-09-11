# tokenwise

Measure where Claude Code tokens actually go, and get told — on screen — when a session has drifted into a new task
while still carrying the old one.

Local, offline, stdlib Python + SQLite. No accounts, no packages, no network calls, no telemetry. Everything it
knows it learns from the transcripts Claude Code already writes on your own machine.

**It never changes your model, your context window, or your permissions.**

macOS and Linux:

```bash
git clone <your-fork-or-this-repo> ~/Projects/tokenwise
cd ~/Projects/tokenwise
./install.sh            # audits itself, then wires the hooks. Nothing to choose.
```

Windows:

```powershell
git clone <your-fork-or-this-repo> $HOME\.claude-tools\tokenwise
cd $HOME\.claude-tools\tokenwise
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Same five steps either way, and the same audit gate. Everything that differs between the three platforms is
detected at run time, so one checkout serves all of them: the desktop notification, the hook command form, and
the console encoding. See [Windows](#windows) below for what is different and why.

Hooks take effect in new Claude Code sessions. `./uninstall.sh` or `.\uninstall.ps1` reverses everything.
`TOKENWISE_OFF=1` disables every handler without uninstalling.

---

## Why: the equation

A session's bill is the sum of the context at every turn, and the context at turn N is everything added in turns
1..N−1. So **X tokens entering at turn N cost X × (T − N)**, where T is the session's last turn — not X.

Call that the **carry cost**. `ledger.py waste` computes it from your own transcripts.

On the machine this was built on, over seven days:

| | |
|---|---|
| total carry | ~324M tokens of cache-read |
| shell-command results | 87% of it, from only 1.8M raw tokens |
| file reads | 9% |
| worst individual results | 2–8K tokens each, re-sent **1,100–1,400 times** |
| carry from results landing with 100+ turns left | **98%** |

And across two months — 52,438 turns in 2,040 sessions:

| session length | sessions | turns | share of cache-read | cost per turn |
|---|---|---|---|---|
| 1–20 | 1,542 | 6,790 | 2% | 42K |
| 21–100 | 418 | 19,007 | 17% | 119K |
| 101–300 | 70 | 9,234 | 15% | 220K |
| 301–1000 | 3 | 1,427 | 4% | 411K |
| **1000+** | **7** | 15,980 | **62%** | **522K** |

**Seven sessions out of 2,040 caused 62% of two months of consumption.** The multiplier is *turns remaining*, not
result size. Capping outputs barely matters; not dragging a finished task's context through the next one does.

Your numbers will differ. That is the point — run `ledger.py waste` and `ledger.py report --week` on your own data
before believing any of this.

---

## What it does

**task_boundary** — the one the data justifies. When a prompt shares under 12% of its content words with the last
four prompts *and* the session is already past 120 turns, it shows a **desktop notification** and tells the model to
say so in one line. If you have a knowledge store configured (optional, see below) it first checks that the finished
work is durable, and reports what a fresh session would still start with — so you know clearing costs you nothing.

**context_governor** — at 150K / 300K / 450K of context, once each, states the size and what each further turn now
costs.

**router** — classifies each prompt (trivial / lookup / small change / heavy) and injects one line of steering:
answer directly, use the symbol index, use one cheap subagent, or keep exploration out of the main thread.

**agent_model_default** — a subagent spawned without an explicit model runs on a cheaper one.

**read_guard** — a whole-file read of a long text file is capped, with the true line count reported so the model
asks for ranges. *Honest status: low value.* It fired once in four days of real use. Kept because it is free.

**where-is** (`/where-is`) — a local symbol index for Java, Kotlin, TS/JS, Python, Go and SQL, so "where is X
defined" costs one line instead of reading files.

**ledger** (`/ledger`) — `report`, `waste` (carry cost), `compare --split "<time>"` (same metrics before and after a
change), `replay` (run the boundary detector over past sessions and price what it would have saved), `actions`.

**harness** — `harness/run.sh --all` runs your own tasks with handlers on and off and records tokens, turns, cost
and a pass/fail check for each arm.

Every handler is **advisory**: it injects text. None of them blocks a tool call, edits your prompt, or decides a
permission. See [SECURITY.md](SECURITY.md) for why that boundary is enforced rather than promised.

## Honest results

Eight real tasks, each run with handlers on and off, cache-read compared: three better, three worse, one tie, one
not comparable. **No reliable per-call effect.** That is why the project is built around carry cost and session
boundaries instead of per-call guards, and why the low-value handlers are labelled as such rather than quietly
kept. Re-run it on your own tasks with `harness/run.sh`.

Replaying the boundary detector against real history suggested 66% of a week's cache-read was avoidable, and 76%
over a month — but that assumes acting on every prompt, so treat it as a ceiling, not a promise. With genuinely
unchanged behaviour the saving is a couple of percent.

## Optional: a knowledge store

`task_boundary` is more useful if it can verify that finished work is written down somewhere before suggesting you
clear. Two stores are understood, in order.

1. A knowledge store of your own, named by `TOKENWISE_STORE_DB` and `TOKENWISE_STORE_CLI`. None ships with this
   repo.
2. Failing that, the memory notes Claude Code already keeps for the project under
   `~/.claude/projects/<project>/memory/`. Nothing to configure: the handler counts the notes in `MEMORY.md`,
   checks whether any were written recently, and says so. Those notes are exactly what a fresh session loads on
   its own, which is what makes "clearing costs you nothing" a checkable claim rather than a hopeful one.

With neither, it still fires and says plainly that it verified nothing.

## Configuration

| variable | default | meaning |
|---|---|---|
| `TOKENWISE_OFF` | unset | disable every handler |
| `TOKENWISE_CTX_THRESHOLDS` | `150000,300000,450000` | governor thresholds |
| `TOKENWISE_BOUNDARY_MIN_TURNS` | `120` | how long a session must be before boundary alerts |
| `TOKENWISE_BOUNDARY_MAX_OVERLAP` | `0.12` | word overlap below which a prompt counts as a new task |
| `TOKENWISE_READ_CAP` | `600` | line cap for un-ranged file reads |
| `TOKENWISE_SUBAGENT_MODEL` | `sonnet` | model for subagents spawned without one |
| `TOKENWISE_DB` | `tokenwise/ledger.db` | ledger location |
| `TOKENWISE_STORE_DB` / `_CLI` | unset | optional knowledge store |
| `TOKENWISE_ACCOUNTS` | on | set `0` to stop recording which account a session belongs to |
| `TOKENWISE_BOUNDARY_COOLDOWN` | `60` | turns to wait before a second boundary alert in one session |
| `TOKENWISE_NOTIFY` | on | set `0` for no desktop notification; the on-screen line still appears |

Every one of these can also live in `config.local.json` at the repo root, which is gitignored and read when the
variable is not set. That matters most on Windows, where a hook is launched by the Claude Code process and
inherits its environment rather than your shell's:

```json
{ "TOKENWISE_CTX_THRESHOLDS": "400000,600000,800000,900000",
  "TOKENWISE_BOUNDARY_COOLDOWN": "60" }
```

## It installs per machine, not per Claude account

This surprises everyone, so it is worth saying plainly: **Claude Code hooks live in `~/.claude/settings.json`, which
is per macOS user.** They apply to every Claude Code session that user starts, whichever Claude account is signed
in — personal, work, or a second org. Signing out and back in changes nothing. Transcripts carry no account field
either, so one ledger covers them all.

tokenwise therefore attributes each session to the account that was active when it started (from `oauthAccount` in
your own `~/.claude.json`) and reports the split:

```
by account:
  Acme Corp                sessions=  93 turns=19662 cache_read= 2.0G out=  3.5M
  gmail                    sessions=  11 turns=  842 cache_read=  38M out=   90K
```

Nothing leaves your machine, and `TOKENWISE_ACCOUNTS=0` turns the attribution off. One caveat: `~/.claude.json` is
global, so two sessions on *different* accounts starting at the same moment can be misattributed. Sequential
switching is accurate.

If you want a hard boundary between two accounts, a separate macOS user account is the only real one.

## Your data

The ledger and symbol index are built from your own transcripts, on your machine, and are **gitignored** — no
database, no repo list, no harness task and no result file is ever committed. Nothing is uploaded anywhere. If you
fork this, check `git status` before your first push anyway.

## Windows

The hooks, the ledger and the symbol index are the same code on every platform. Three things cannot be:

| | macOS | Linux | Windows |
|---|---|---|---|
| notification | `osascript` | `notify-send` if present | a toast via `tokenwise/notify.ps1`, hashed in the manifest like every other wired file |
| hook command | `/usr/bin/env python3 "<hook>"` | same | `{"command": "<python.exe>", "args": ["<hook>"]}` |
| console | already UTF-8 | already UTF-8 | stdout is reconfigured, or the reports die on a legacy code page |

The hook command is the one that bites. There is no `/usr/bin/env` on Windows; `python3` is usually a Microsoft
Store stub that prints "Python was not found" and exits 0, which Claude Code would then inject into your context
on **every prompt**; and the command may be handed to PowerShell, where a quoted path at the start of a line is a
string literal rather than a program. So `install.ps1` wires the exec form instead, with the absolute interpreter
it verified, and no shell is involved at all.

`install.ps1` refuses to run from an elevated shell, for the same reason `install.sh` refuses root.

## Requirements

macOS, Linux or Windows 10/11. Python 3.9+ (stdlib only) and Claude Code. On Windows use the real python.org
interpreter, not the Microsoft Store alias; `install.ps1` checks and refuses the stub.

## Licence

MIT. Contributions welcome — read [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) first, because
hooks are code that runs automatically and the bar for merging them is deliberately high.
