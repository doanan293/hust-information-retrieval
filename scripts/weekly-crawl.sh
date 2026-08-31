#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p data
set +e
flock -n data/.weekly.lock docker compose run --rm crawler crawl
status=$?
set -e
if [[ $status -eq 3 ]]; then
  echo "crawl completed with coverage gaps" >&2
fi
exit "$status"
