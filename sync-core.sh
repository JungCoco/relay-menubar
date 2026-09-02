#!/usr/bin/env bash
# core.py 벤더링 동기화 — 원본은 relay-account-pool/switch/core.py.
#   ./sync-core.sh          드리프트 확인만 (diff)
#   ./sync-core.sh --pull   원본(account-pool) → 이 레포로 복사
#   ./sync-core.sh --push   이 레포 → 원본(account-pool)으로 복사
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
UPSTREAM="${POOL_REPO:-$HOME/relay-account-pool}/switch/core.py"
LOCAL="$ROOT/core.py"

[ -f "$UPSTREAM" ] || { echo "원본 없음: $UPSTREAM (POOL_REPO 환경변수로 지정 가능)"; exit 1; }

case "${1:-}" in
  --pull) cp "$UPSTREAM" "$LOCAL"; echo "pull 완료: $UPSTREAM → core.py" ;;
  --push) cp "$LOCAL" "$UPSTREAM"; echo "push 완료: core.py → $UPSTREAM" ;;
  *)
    if diff -u "$UPSTREAM" "$LOCAL"; then
      echo "드리프트 없음 ✓"
    else
      echo; echo "드리프트 있음 — --pull 또는 --push 로 동기화하세요."; exit 1
    fi ;;
esac
