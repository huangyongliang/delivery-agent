#!/bin/sh
# Submit one mock-mode delivery and print the finished record.
set -eu
BASE="${BASE_URL:-http://127.0.0.1:8080}"
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
echo "POST ${BASE}/api/v1/deliveries?wait=true"
curl -sS -X POST "${BASE}/api/v1/deliveries?wait=true" \
  -H "Content-Type: application/json" \
  --data-binary "@${ROOT}/examples/demo-request.json"
echo
