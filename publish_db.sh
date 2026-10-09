#!/bin/bash
# 로컬 kbo.db를 압축해서 GitHub 릴리스(data)에 올린다.
# 배포된 앱이 30분 안에 이 파일을 새로 내려받아 최신 기록으로 바뀐다.
# 사용: fetch_kbo.py로 기록을 갱신한 뒤 ./publish_db.sh
set -e
cd "$(dirname "$0")"
REPO="jun-yoonsung/kbo-dashboard"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

sqlite3 kbo.db ".backup '$TMP/kbo.db'"   # 쓰는 중에도 일관된 스냅샷을 만든다
gzip -9 "$TMP/kbo.db"

if ! gh release view data --repo "$REPO" >/dev/null 2>&1; then
  gh release create data --repo "$REPO" --title "KBO 데이터 스냅샷" \
    --notes "배포된 대시보드가 내려받는 kbo.db 압축본입니다. publish_db.sh가 갱신합니다."
fi
gh release upload data "$TMP/kbo.db.gz" --repo "$REPO" --clobber
echo "업로드 완료: $(du -h "$TMP/kbo.db.gz" | cut -f1)"
