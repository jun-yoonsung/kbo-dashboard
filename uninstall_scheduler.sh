#!/bin/bash
TARGET="$HOME/Library/LaunchAgents/com.yoonsung.kbo-fetch.plist"
launchctl unload "$TARGET" 2>/dev/null || true
rm -f "$TARGET"
echo "자동 수집 작업을 해제했습니다."
