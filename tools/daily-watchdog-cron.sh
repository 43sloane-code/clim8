#!/usr/bin/env bash
# Daily REGRESSION WATCHDOG -- deterministic-first, LLM-on-escalation.
# Runs watchdog_core.py (stdlib, zero tokens) for Duties 1-3; wakes Claude ONLY
# on a non-green core exit (RED/ABORT). Read-only on the pipeline. Repo: clim8.
#
# CORRECTED vs the first draft:
#   * The crossover producer is tools/intraday_ceiling_backtest.py (the shipped
#     edge's hit-rate-by-hour), NOT ab_backtest.py (which does day-ahead member
#     A/B -- a different instrument). ab_backtest.py stays only as the LLM's
#     investigative tool on escalation.
#   * The emit is PINNED to --end so the daily check evaluates the SAME held-out
#     days as the baseline -> only a code change can move the number.
#   * Per-city loop maps city-name keys (intraday_ceiling_backtest --city) while
#     the core takes ICAO (--cities); keep WX_CITIES and WX_XOVER_CITIES in sync.
#
# REGRESSION-CHECK CONTRACT (read before changing baselines):
#   - WX_BASELINE is the ref UNDER TEST (typically the deployed tag / main HEAD),
#     pinned so mid-run commits can't shift the result.
#   - reports/crossover_baseline.json holds the FROZEN known-good hit-rates,
#     committed once and carried forward UNCHANGED while code evolves. The check
#     is: current code's emit (at WX_BASELINE) vs those frozen numbers. Do NOT
#     regenerate it per commit -- regenerate only on a deliberate rebaseline
#     (delete it -> Duty 2 ABSTAINs and adopts the next clean run), and re-pin
#     WX_XOVER_END to the new window.
#
# PRECONDITIONS: the watchdog files (tools/watchdog_core.py, the --emit-crossover
# flag in tools/intraday_ceiling_backtest.py, ledger/dead_candidates.jsonl,
# reports/crossover_baseline.json) must be COMMITTED at WX_BASELINE -- the run
# refuses a dirty tree and checks out that ref.
set -euo pipefail

# ---- Secrets: NEVER on the crontab line. Sourced from a 600 env file. ----
ENV_FILE="${WX_ENV_FILE:-$HOME/.wx-loop.env}"
if [ -f "$ENV_FILE" ]; then
  perms="$(stat -f '%Lp' "$ENV_FILE" 2>/dev/null || stat -c '%a' "$ENV_FILE" 2>/dev/null || echo '')"
  if [ -n "$perms" ] && [ "$perms" != "600" ] && [ "$perms" != "400" ]; then
    echo "[$(date -Is)] $ENV_FILE is $perms; must be 600/400. Refusing." >&2; exit 1
  fi
  set -a; . "$ENV_FILE"; set +a
fi

# ---- Config: real repo facts. LITERAL SPACE in the path. ----
REPO="${WX_REPO:-/Users/43slauson/Desktop/mock projects/weather-verdict}"
BASELINE_REF="${WX_BASELINE:-}"
MODEL="${WX_MODEL:-claude-opus-4-8}"
MAX_TURNS="${WX_MAX_TURNS:-25}"           # LLM only does Duty 4 judgment; small cap is plenty
COST_CAP_USD="${WX_COST_CAP_USD:-3.00}"
PYTHON_BIN="${WX_PYTHON:-python3}"
HARNESS="${WX_HARNESS:-tools/ab_backtest.py}"          # LLM's day-ahead A/B tool (escalation only)
CORE="${WX_CORE:-tools/watchdog_core.py}"
XOVER="${WX_XOVER:-tools/intraday_ceiling_backtest.py}" # crossover producer (the shipped edge)
XOVER_END="${WX_XOVER_END:-2026-06-20}"               # MUST match reports/crossover_baseline.json's pin
XOVER_HOURS="${WX_XOVER_HOURS:-12,13,14,15}"
XOVER_CITIES="${WX_XOVER_CITIES:-singapore manila}"   # name keys; must mirror WX_CITIES (WSSS,RPLL)
STATE_DOC="${WX_STATE_DOC:-$REPO/SESSION_STATE.md}"
CITIES="${WX_CITIES:-RPLL,WSSS}"                       # ICAO keys for the core (--cities)
LOCK="$REPO/.watchdog.lock"
DATE="$(date +%F)"
LOG_DIR="$REPO/ledger/runs"

[ -n "${ANTHROPIC_API_KEY:-}" ] || { echo "[$(date -Is)] ANTHROPIC_API_KEY unset (expected in $ENV_FILE)." >&2; exit 1; }
[ -n "$BASELINE_REF" ] || { echo "[$(date -Is)] WX_BASELINE unset -- pin the ref under test." >&2; exit 1; }

cd "$REPO"

# ---- Stale-lock-aware single instance ----
if [ -e "$LOCK" ]; then
  op="$(cat "$LOCK" 2>/dev/null || echo '')"
  if [ -n "$op" ] && kill -0 "$op" 2>/dev/null; then echo "[$(date -Is)] running (pid $op), exit" >&2; exit 0; fi
  echo "[$(date -Is)] stale lock pid $op -- reclaiming" >&2; rm -f "$LOCK"
fi
trap 'rm -f "$LOCK"' EXIT; echo "$$" > "$LOCK"

[ -e "ledger/.cost-halt" ] && { echo "[$(date -Is)] .cost-halt present -- refusing." >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "[$(date -Is)] dirty tree, refusing." >&2; exit 1; }

# ---- Pre-flight paths ----
miss=0
for p in "$HARNESS" "$CORE" "$XOVER" "weather_council/intraday_ceiling.py" \
         "reports/crossover_baseline.json" "ledger/dead_candidates.jsonl"; do
  [ -e "$p" ] || { echo "[$(date -Is)] missing: $p" >&2; miss=1; }
done
[ "$miss" -eq 0 ] || { echo "[$(date -Is)] path pre-flight failed." >&2; exit 1; }
command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "[$(date -Is)] $PYTHON_BIN not found." >&2; exit 1; }

# ---- Pin ref under test; tests-green floor before anything ----
git checkout "$BASELINE_REF"
PYTHONPATH=. "$PYTHON_BIN" -m unittest discover -s tests >/dev/null 2>"$LOG_DIR/$DATE-tests.log" \
  || { echo "[$(date -Is)] test floor FAILED on $BASELINE_REF -- aborting." >&2; exit 1; }

mkdir -p "$LOG_DIR" "$REPO/ledger/traces"

# ============================================================================
# STEP 1 -- CANARY GATE. The detector must trip RED on known-bad input, or a
# GREEN today means nothing. Refuse to proceed on a broken detector.
# ============================================================================
if ! "$PYTHON_BIN" "$CORE" --canary >"$LOG_DIR/$DATE-canary.json" 2>>"$LOG_DIR/$DATE-canary.json"; then
  echo "[$(date -Is)] CANARY FAILED -- detector broken, refusing to run watchdog." >&2; exit 1
fi

# ============================================================================
# STEP 2 -- DETERMINISTIC CORE (Duties 1-3, zero LLM, zero tokens).
# ============================================================================
# 2a. Crossover producer: emit the shipped edge's hit-rate-by-hour for each
#     basket city, PINNED to the baseline window, merged into one file keyed by
#     ICAO. This is the apples-to-apples current-run vs frozen-baseline input.
AB_NOW="$LOG_DIR/$DATE-crossover-now.json"
rm -f "$AB_NOW"
for cty in $XOVER_CITIES; do
  PYTHONPATH=. "$PYTHON_BIN" "$XOVER" --city "$cty" --end "$XOVER_END" \
      --hours "$XOVER_HOURS" --emit-crossover "$AB_NOW" \
      >>"$LOG_DIR/$DATE-xover.log" 2>&1 \
    || echo "[$(date -Is)] crossover emit failed for $cty (Duty 2 will flag the missing city)" >&2
done

# 2b. Truth-source config for Duty 3. resolve_truth_sources.py is not wired yet;
#     write [] so Duty 3 leans on --ecmwf-bias (or ABSTAINs) rather than crashing.
TRUTH_CFG="$LOG_DIR/$DATE-truth.json"
if [ -f "tools/resolve_truth_sources.py" ]; then
  PYTHONPATH=. "$PYTHON_BIN" tools/resolve_truth_sources.py > "$TRUTH_CFG" 2>/dev/null || echo "[]" > "$TRUTH_CFG"
else
  echo "[]" > "$TRUTH_CFG"
fi

# 2c. Run the core. Pass --ecmwf-bias only when measured (else Duty 3 ABSTAINs).
set +e
if [ -n "${WX_ECMWF_BIAS:-}" ]; then
  "$PYTHON_BIN" "$CORE" --repo "$REPO" --cities "$CITIES" \
    --ab-now "$AB_NOW" --truth-config "$TRUTH_CFG" --ecmwf-bias "$WX_ECMWF_BIAS" \
    --out "$LOG_DIR/$DATE-core.json"
else
  "$PYTHON_BIN" "$CORE" --repo "$REPO" --cities "$CITIES" \
    --ab-now "$AB_NOW" --truth-config "$TRUTH_CFG" \
    --out "$LOG_DIR/$DATE-core.json"
fi
CORE_EXIT=$?
set -e
echo "[$(date -Is)] core exit=$CORE_EXIT (0=GREEN 2=AMBER 3=RED 4=ABORT)"

# ---- Pure GREEN/AMBER needs no LLM at all. ----
if [ "$CORE_EXIT" -eq 0 ] || [ "$CORE_EXIT" -eq 2 ]; then
  echo "[$(date -Is)] core GREEN/AMBER -- no LLM needed. Done. (cost: \$0)"
  exit 0
fi

# ============================================================================
# STEP 3 -- ESCALATE TO LLM (only on RED/ABORT). Duty 4 judgment + dead-candidate
# check + one-line restoration hypothesis. Read-mostly toolset.
# (Requires a /daily-watchdog skill, or replace with an inline prompt.)
# ============================================================================
WX_BASELINE="$BASELINE_REF" WX_PYTHON="$PYTHON_BIN" WX_HARNESS="$HARNESS" WX_STATE_DOC="$STATE_DOC" \
WX_CORE_REPORT="$LOG_DIR/$DATE-core.json" \
claude -p "/daily-watchdog (core escalated; read ledger/runs/$DATE-core.json; do Duty 4 only)" \
  --bare --model "$MODEL" --max-turns "$MAX_TURNS" \
  --permission-mode acceptEdits \
  --allowedTools "Read,Grep,Glob,Write(ledger/*),Bash(git log:*),Bash(git diff:*),Bash(${PYTHON_BIN} ${HARNESS}:*),Bash(${PYTHON_BIN} ${XOVER}:*)" \
  --disallowedTools "Bash(git push:*),Bash(git merge:*),Bash(git rebase:*),Bash(git commit:*),Bash(rm:*),Edit,Write(weather_council/*),Write(run.py),Write(${HARNESS})" \
  --output-format json \
  > "$LOG_DIR/$DATE-watchdog-result.json" 2> "$LOG_DIR/$DATE-watchdog-stderr.log" || true

if command -v jq >/dev/null 2>&1; then
  cost="$(jq -r '.total_cost_usd // 0' "$LOG_DIR/$DATE-watchdog-result.json" 2>/dev/null)"
  echo "[$(date -Is)] LLM escalation cost_usd=$cost (cap $COST_CAP_USD)"
  awk "BEGIN{exit !($cost > $COST_CAP_USD)}" && { echo "[$(date -Is)] COST CAP BREACHED -- halt marker" >&2; echo "$DATE cost=$cost" > "ledger/.cost-halt"; }
fi
echo "[$(date -Is)] watchdog done (escalated). RED/ABORT report -> $LOG_DIR/$DATE-watchdog-result.json"

# ---- Crontab (secrets in $ENV_FILE). Host TZ must be pinned; SGT/PHT are UTC+8.
#      Run after both cities' 14:00 lock + next-day settlement, in HOST local time.
#      WX_XOVER_END must match the committed crossover_baseline.json pin.
#   30 16 * * *  TZ=Asia/Singapore "<repo>/tools/daily-watchdog-cron.sh" >> "<repo>/ledger/cron.log" 2>&1
