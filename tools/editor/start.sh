#!/usr/bin/env bash
# Chirpy 포스트 편집기 실행 스크립트
# 사용법:  bash tools/editor/start.sh          (저장소 루트에서 실행)
set -e

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"

echo "▶ 저장소: $REPO"

# 1) 파이썬 찾기
PY=python3
command -v python3 >/dev/null || PY=python
command -v $PY >/dev/null || { echo "✗ python 을 찾을 수 없습니다"; exit 1; }

PYV=$($PY -c 'import sys;print("%d.%d"%sys.version_info[:2])')
echo "▶ 파이썬: $($PY -c 'import sys;print(sys.executable)') ($PYV)"
$PY -c 'import sys;sys.exit(0 if sys.version_info>=(3,8) else 1)' \
  || { echo "✗ 파이썬 3.8 이상이 필요합니다 (현재 $PYV)"; exit 1; }

# 2) pip 없으면 설치 (Jekyll devcontainer 이미지엔 pip 이 빠져 있다)
has_pip(){ $PY -m pip --version >/dev/null 2>&1; }

if ! has_pip; then
  echo "▶ pip 이 없습니다. 설치를 시도합니다…"

  # (a) 표준 방법
  $PY -m ensurepip --upgrade >/dev/null 2>&1 || true

  # (b) get-pip.py — 파이썬 버전별 URL을 써야 한다.
  #     3.9 이하는 bootstrap.pypa.io/pip/<버전>/get-pip.py 를 요구함.
  if ! has_pip && command -v curl >/dev/null 2>&1; then
    case "$PYV" in
      3.6|3.7|3.8|3.9) GETPIP="https://bootstrap.pypa.io/pip/$PYV/get-pip.py" ;;
      *)               GETPIP="https://bootstrap.pypa.io/get-pip.py" ;;
    esac
    echo "  get-pip.py 내려받는 중… ($GETPIP)"
    if curl -fsSL "$GETPIP" -o /tmp/get-pip.py; then
      $PY /tmp/get-pip.py --user >/dev/null 2>&1 || $PY /tmp/get-pip.py >/dev/null 2>&1 || true
    fi
  fi

  # (c) apt — bullseye 는 저장소가 만료돼서 update 가 실패한다.
  #     Check-Valid-Until 을 끄고, update 실패해도 install 은 시도한다.
  if ! has_pip && command -v apt-get >/dev/null 2>&1; then
    echo "  apt-get 으로 시도 중…"
    sudo apt-get update -qq -o Acquire::Check-Valid-Until=false >/dev/null 2>&1 || true
    sudo apt-get install -y -qq python3-pip >/dev/null 2>&1 || true
  fi
fi

has_pip || {
  cat <<'MSG'
✗ pip 설치에 실패했습니다. 아래를 직접 실행해 보세요.

    curl -fsSL https://bootstrap.pypa.io/pip/3.9/get-pip.py -o /tmp/get-pip.py
    python3 /tmp/get-pip.py --user

MSG
  exit 1
}
echo "▶ pip: $($PY -m pip --version)"

# 3) 파이썬 패키지 (PEP 668 / 권한 문제 대비 3단 폴백)
echo "▶ 파이썬 패키지 확인…"
REQ="$HERE/requirements.txt"
$PY -m pip install -q -r "$REQ" \
  || $PY -m pip install -q --break-system-packages -r "$REQ" \
  || $PY -m pip install -q --user -r "$REQ" \
  || { echo "✗ 패키지 설치 실패"; exit 1; }

# 4) Ruby 의존성 (없을 때만)
if [ -f Gemfile ] && command -v bundle >/dev/null 2>&1; then
  if ! bundle check >/dev/null 2>&1; then
    echo "▶ bundle install 실행 중… (처음 한 번만, 1~3분)"
    bundle install
  fi
elif [ -f Gemfile ]; then
  echo "⚠ bundle 명령이 없습니다. Jekyll 미리보기는 안 되고 편집 기능만 동작합니다."
fi

# 5) 실행
echo ""
echo "───────────────────────────────────────────────"
echo "  편집기:  http://localhost:${EDITOR_PORT:-5000}/app"
echo "  Codespace라면 PORTS 탭에서 ${EDITOR_PORT:-5000} 포트를 여세요."
echo "  (Jekyll 4000 포트는 편집기가 대신 프록시하므로 열 필요 없습니다)"
echo "───────────────────────────────────────────────"
echo ""
exec $PY "$HERE/app.py"
