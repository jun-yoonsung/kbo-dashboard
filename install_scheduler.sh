#!/bin/bash
# 매일 오전 9시(KST)에 fetch_kbo.py를 자동 실행하는 launchd 작업을 등록한다.
set -e
cd "$(dirname "$0")"

PLIST_NAME="com.yoonsung.kbo-fetch.plist"
TARGET="$HOME/Library/LaunchAgents/$PLIST_NAME"

mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST_NAME" "$TARGET"

launchctl unload "$TARGET" 2>/dev/null || true
launchctl load "$TARGET"

echo "등록 완료: 매일 09:00에 자동으로 KBO 기록을 수집합니다."
echo "확인: launchctl list | grep kbo-fetch"
echo "해제하려면: ./uninstall_scheduler.sh"
