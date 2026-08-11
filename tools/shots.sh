#!/bin/bash
# 루프 엔지니어링용 스크린샷 하베스트: 5탭 × 데스크톱/모바일
set -e
CHROME=/opt/pw-browsers/chromium-1194/chrome-linux/chrome
OUT=${1:-/tmp/shots}; PORT=${2:-8231}
mkdir -p "$OUT"
( cd docs && python3 -m http.server $PORT >/tmp/shots_srv.log 2>&1 & echo $! > /tmp/shots_srv.pid )
sleep 1.2
shot(){ $CHROME --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
  --virtual-time-budget=4500 --window-size=$3 --screenshot="$OUT/$2.png" \
  "http://localhost:$PORT/$1" 2>/dev/null; echo "  $2.png $(wc -c <"$OUT/$2.png")B"; }
for tab in briefing list issues insights stats; do
  shot "#$tab" "d_$tab" "1280,1000"
  shot "#$tab" "m_$tab" "500,1600"
done
kill "$(cat /tmp/shots_srv.pid)" 2>/dev/null || true
