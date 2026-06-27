#!/usr/bin/env bash
# Daily full-stack Singapore verdict -> a dated report under reports/.
# Run by launchd at 09:00 SGT (day-ahead band) and 15:00 SGT (post-peak locked
# single bucket). Self-contained; writes one timestamped report per fire.
#
# Sections: (1) council verdict (Bayesian/Monte-Carlo ensemble + intraday lever +
# market compare + live scorecard), (2) intraday-ceiling validation gate,
# (3) live+historical WU pattern recognition. run.py targets the city's OWN civil
# day (place_today), so lead 0 is always Singapore-today regardless of host clock.
set -uo pipefail
REPO="/Users/43slauson/Desktop/mock projects/weather-verdict"
PY="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
cd "$REPO" || { echo "repo missing"; exit 1; }

SGT_DATE="$(TZ=Asia/Singapore date +%F)"
SGT_HM="$(TZ=Asia/Singapore date +%H%M)"
OUT="$REPO/reports/verdict-singapore-${SGT_DATE}-${SGT_HM}sgt.txt"
mkdir -p "$REPO/reports"

{
  echo "===== SINGAPORE FULL-STACK VERDICT — ${SGT_DATE} ${SGT_HM} SGT ====="
  echo
  echo "### 1. COUNCIL VERDICT (8 NWP + 92-member ensemble, Bayesian bias + Monte-Carlo"
  echo "###    bucket pmf, live intraday lever, market compare, live scorecard) ###"
  PYTHONPATH=. "$PY" run.py "Singapore" --lead 0 --market --intraday 2>&1
  echo
  echo "### 2. INTRADAY VALIDATION (WU-native ceiling lever, disjoint-fold gate) ###"
  PYTHONPATH=. "$PY" tools/intraday_ceiling_backtest.py --city singapore --hours 13,14,15,16 2>&1
  echo
  echo "### 3. PATTERN RECOGNITION (live + historical Wunderground) ###"
  PYTHONPATH=. "$PY" tools/wu_pattern.py --city singapore 2>&1
  echo
  echo "===== END (generated $(date '+%Y-%m-%dT%H:%M:%S%z')) ====="
} > "$OUT" 2>&1

echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] wrote $OUT"
