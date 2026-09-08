#!/bin/bash
# tokenwise replay harness: run each task twice — handlers ON (normal) and OFF (TOKENWISE_OFF=1) — and print
# tokens, turns, cost and the check result side by side. Every run spends real usage; nothing here is scheduled.
#
#   run.sh --dry                  list tasks
#   run.sh --task t1-whereis      one task, both arms
#   run.sh --all                  every task, both arms
#   run.sh --all --arm on|off     one arm only
#
# Tasks live in tasks.jsonl: {"id","cwd","prompt","check"}; check is a shell snippet with $OUT = file holding the reply.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
HERE="$(cd "$(dirname "$0")" && pwd)"
TASKS="$HERE/tasks.jsonl"
RESULTS="$HERE/results.tsv"
ARMS="on off"; ONLY=""; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry) DRY=1; shift ;;
    --task) ONLY="$2"; shift 2 ;;
    --all) shift ;;
    --arm) ARMS="$2"; shift 2 ;;
    *) echo "unknown arg $1"; exit 2 ;;
  esac
done
[ -f "$RESULTS" ] || printf 'ts\ttask\tarm\tmodel\tturns\tin\tcache_write\tcache_read\tout\tcost_usd\tseconds\tcheck\n' > "$RESULTS"

run_one() {  # id cwd prompt check arm
  local id="$1" cwd="$2" prompt="$3" check="$4" arm="$5"
  local out json rc t0 t1
  out=$(mktemp); json=$(mktemp)
  t0=$(date +%s)
  if [ "$arm" = "off" ]; then export TOKENWISE_OFF=1; else unset TOKENWISE_OFF; fi
  ( cd "$cwd" && printf '%s' "$prompt" | claude -p --output-format json --allowedTools Read Grep Glob Bash > "$json" 2>/dev/null )
  rc=$?
  t1=$(date +%s)
  python3 -c 'import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
open(sys.argv[2],"w").write(str(d.get("result","")))' "$json" "$out"
  METRICS=$(python3 - "$json" <<'PY'
import json, sys
try: d = json.load(open(sys.argv[1]))
except Exception: d = {}
u = d.get('usage') or {}
mu = d.get('modelUsage') or {}
model = next(iter(mu), '?') if isinstance(mu, dict) else '?'
print('\t'.join(str(x) for x in (model, d.get('num_turns', ''), u.get('input_tokens', 0), u.get('cache_creation_input_tokens', 0),
      u.get('cache_read_input_tokens', 0), u.get('output_tokens', 0), round(d.get('total_cost_usd') or 0, 4))))
PY
)
  if OUT="$out" bash -c "$check" 2>/dev/null; then CHK=pass; else CHK=FAIL; fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date '+%F %T')" "$id" "$arm" "$METRICS" "$((t1 - t0))" "$CHK" >> "$RESULTS"
  printf '%-14s %-4s %s  %ss  %s\n' "$id" "$arm" "$METRICS" "$((t1 - t0))" "$CHK"
  rm -f "$out" "$json"
}

echo "task           arm  model  turns  in  cache_write  cache_read  out  cost  secs  check"
while IFS= read -r line; do
  [ -z "$line" ] && continue
  id=$(printf '%s' "$line" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  [ -n "$ONLY" ] && [ "$ONLY" != "$id" ] && continue
  cwd=$(printf '%s' "$line" | python3 -c 'import json,sys,os; print(os.path.expanduser(json.load(sys.stdin)["cwd"]))')
  prompt=$(printf '%s' "$line" | python3 -c 'import json,sys; print(json.load(sys.stdin)["prompt"])')
  check=$(printf '%s' "$line" | python3 -c 'import json,sys; print(json.load(sys.stdin)["check"])')
  if [ "$DRY" = 1 ]; then echo "$id  [$cwd]  $prompt"; continue; fi
  for arm in $ARMS; do run_one "$id" "$cwd" "$prompt" "$check" "$arm"; done
done < "$TASKS"
[ "$DRY" = 1 ] || echo "appended to $RESULTS"
