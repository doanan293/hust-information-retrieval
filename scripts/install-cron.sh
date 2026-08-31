#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "$0")/.." && pwd)
entry="0 2 * * 0 $project_dir/scripts/weekly-crawl.sh >> $project_dir/data/cron.log 2>&1"
(crontab -l 2>/dev/null | grep -v -F "$project_dir/scripts/weekly-crawl.sh" || true; printf '%s\n' "$entry") | crontab -
echo "Installed weekly crawl at Sunday 02:00 local time"

