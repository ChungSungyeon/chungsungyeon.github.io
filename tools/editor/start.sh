#!/usr/bin/env bash
# Chirpy 포스트 편집기 실행 스크립트
# 사용법:  bash tools/editor/start.sh          (저장소 루트에서 실행)
set -e

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"

echo "▶ 저장소: $REPO"

# 1) 파이썬 의존성
PY=python3
command -v python3 >/dev/null || PY=python
echo "▶ 파이썬 패키지 확인…"
$PY -m pip install -q -r "$HERE/requirements.txt"

# 2) Ruby 의존성 (없을 때만)
if [ -f Gemfile ]; then
  if ! bundle check >/dev/null 2>&1; then
    echo "▶ bundle install 실행 중… (처음 한 번만, 1~3분)"
    bundle install
  fi
fi

# 3) 실행
echo ""
echo "───────────────────────────────────────────────"
echo "  편집기:  http://localhost:${EDITOR_PORT:-5000}/app"
echo "  Codespace라면 PORTS 탭에서 ${EDITOR_PORT:-5000} 포트를 여세요."
echo "  (Jekyll 4000 포트는 편집기가 대신 프록시하므로 열 필요 없습니다)"
echo "───────────────────────────────────────────────"
echo ""
exec $PY "$HERE/app.py"
