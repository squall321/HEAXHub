#!/usr/bin/env bash
# heax-demo-r — job_runner entrypoint.
#
# HEAXHub job orchestrator interface:
#   $1 : input dir   (uploaded files; unused for this demo)
#   $2 : output dir  (where artifacts must be written)
#   $3 : params.json (form values: n, seed, use_ggplot)
#
# Exit 0 on success.
set -euo pipefail

INPUT_DIR="${1:-./input}"
OUTPUT_DIR="${2:-./output}"
PARAMS_JSON="${3:-./params.json}"

cd "$(dirname "$0")/.."   # repo root
mkdir -p "$OUTPUT_DIR"

# Pull n / seed out of params.json if present. Plain shell — no jq dependency.
extract() {
  local key="$1" default="$2"
  if [[ -f "$PARAMS_JSON" ]]; then
    python3 - "$PARAMS_JSON" "$key" "$default" <<'PY' 2>/dev/null || echo "$default"
import json, sys
path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path) as f:
        data = json.load(f)
    v = data.get(key, default)
    print(v if v not in (None, "") else default)
except Exception:
    print(default)
PY
  else
    echo "$default"
  fi
}

N="$(extract n 200)"
SEED="$(extract seed 42)"

exec Rscript run.R --n "$N" --seed "$SEED" --output "$OUTPUT_DIR"
