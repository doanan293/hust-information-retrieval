#!/usr/bin/env bash
#
# Tải VnCoreNLP (bộ tách từ tiếng Việt) — cần cho: --analyzer vietnamese (mặc định).
# Tải về:
#   lib/VnCoreNLP-1.2.jar                         (~27 MB)
#   models/wordsegmenter/wordsegmenter.rdr        (~128 KB)
#   models/wordsegmenter/vi-vocab                 (~515 KB)
# Cả lib/ và models/ đều đã nằm trong .gitignore.
#
set -euo pipefail

BASE="https://raw.githubusercontent.com/vncorenlp/VnCoreNLP/master"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$ROOT/lib" "$ROOT/models/wordsegmenter"

fetch() {  # fetch <url> <dest>
  local url="$1" dest="$2"
  if [[ -s "$dest" ]]; then
    echo "  đã có : ${dest#"$ROOT"/}"
  else
    echo "  tải   : ${dest#"$ROOT"/}"
    curl -fL --progress-bar -o "$dest" "$url"
  fi
}

echo "== Tải VnCoreNLP =="
fetch "$BASE/VnCoreNLP-1.2.jar"                      "$ROOT/lib/VnCoreNLP-1.2.jar"
fetch "$BASE/models/wordsegmenter/wordsegmenter.rdr" "$ROOT/models/wordsegmenter/wordsegmenter.rdr"
fetch "$BASE/models/wordsegmenter/vi-vocab"          "$ROOT/models/wordsegmenter/vi-vocab"

echo
echo "Xong. Kiểm tra:"
echo "  mvn -q package"
echo "  java -jar target/lucene-search.jar --mode batch --queries data/queries-vi.txt"
