# Tai VnCoreNLP (bo tach tu tieng Viet) - can cho: --analyzer vietnamese (mac dinh).
# Chay:  powershell -ExecutionPolicy Bypass -File scripts\setup-vncorenlp.ps1
#
# Tai ve:
#   lib\VnCoreNLP-1.2.jar                    (~27 MB)
#   models\wordsegmenter\wordsegmenter.rdr   (~128 KB)
#   models\wordsegmenter\vi-vocab           (~515 KB)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest nhanh hon

$base = "https://raw.githubusercontent.com/vncorenlp/VnCoreNLP/master"
$root = Split-Path -Parent $PSScriptRoot

New-Item -ItemType Directory -Force -Path "$root\lib" | Out-Null
New-Item -ItemType Directory -Force -Path "$root\models\wordsegmenter" | Out-Null

function Fetch($url, $dest) {
    if ((Test-Path $dest -PathType Leaf) -and ((Get-Item $dest).Length -gt 0)) {
        Write-Host "  da co : $dest"
        return
    }
    Write-Host "  tai   : $dest"
    Invoke-WebRequest -Uri $url -OutFile $dest
}

Write-Host "== Tai VnCoreNLP =="
Fetch "$base/VnCoreNLP-1.2.jar"                      "$root\lib\VnCoreNLP-1.2.jar"
Fetch "$base/models/wordsegmenter/wordsegmenter.rdr" "$root\models\wordsegmenter\wordsegmenter.rdr"
Fetch "$base/models/wordsegmenter/vi-vocab"          "$root\models\wordsegmenter\vi-vocab"

Write-Host ""
Write-Host "Xong. Kiem tra:"
Write-Host "  mvn -q package"
Write-Host "  java -jar target\lucene-search.jar --mode batch --queries data\queries-vi.txt"
