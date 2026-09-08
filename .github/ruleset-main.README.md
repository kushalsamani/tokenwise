How to use: Settings → Rules → Rulesets → New ruleset → Import a ruleset → pick .github/ruleset-main.json

What it does
  deletion         main cannot be deleted
  non_fast_forward main cannot be force-pushed (history stays honest)
  pull_request     changes arrive via PR. 0 approvals required, because a solo maintainer cannot
                   approve their own PR and would otherwise be locked out. CODEOWNERS review IS
                   required, which means a PR touching audit.py / tools/ / install.sh / .github/
                   needs the owner's review — exactly the files SECURITY.md calls the boundary.
                   Stale approvals are dismissed when someone pushes more commits.
  required_status  the three CI jobs must pass: security audit, hook behaviour tests, clean install

Bypass: repository admin (you) can still push straight to main when you want. Remove the
bypass_actors block if you would rather hold yourself to the same rule.

Two notes
  1. Status checks only become selectable/enforceable after CI has run once on the repo. Push a
     commit or open a PR first, let the workflow run, then import.
  2. To watch it before it bites, change "enforcement" to "evaluate" — GitHub records what would
     have been blocked without blocking anything. Switch back to "active" when the log looks right.
