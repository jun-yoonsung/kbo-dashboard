"""배포 환경용 DB 동기화.

kbo.db(100MB+)는 GitHub가 100MB 넘는 파일을 막아 저장소에 못 올린다. 대신 publish_db.sh가
gzip으로 압축해(약 23MB) GitHub 릴리스(data)에 올려두고, 배포된 앱이 시작할 때·주기적으로
그 파일을 내려받는다.

로컬 개발 환경의 kbo.db는 절대 덮어쓰지 않는다 — 이 모듈이 내려받은 DB에만 'kbo.remote'
마커 파일이 생기고, 마커가 없는 기존 DB는 그대로 둔다(fetch_kbo.py가 쓰는 원본이기 때문).
"""

import gzip
import os
import shutil
import urllib.request
from pathlib import Path

REPO = "jun-yoonsung/kbo-dashboard"
ASSET_URL = f"https://github.com/{REPO}/releases/download/data/kbo.db.gz"
UA = {"User-Agent": "kbo-dashboard"}


def _remote_version():
    """릴리스 파일이 바뀌었는지 비교할 값(ETag 우선). GitHub API는 비로그인 시간당 호출 제한이
    있어 쓰지 않고 다운로드 URL에 HEAD 요청만 보낸다."""
    req = urllib.request.Request(ASSET_URL, headers=UA, method="HEAD")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.headers.get("ETag") or r.headers.get("Last-Modified") or ""


def sync_db(db_path: Path):
    """'local'(로컬 DB라 건드리지 않음) / 'current'(이미 최신) / 'updated'(새로 내려받음)을 반환."""
    marker = db_path.with_suffix(".remote")
    if db_path.exists() and not marker.exists():
        return "local"

    version = _remote_version()
    if db_path.exists() and marker.exists() and marker.read_text() == version:
        return "current"

    tmp = db_path.with_suffix(".tmp")
    req = urllib.request.Request(ASSET_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, gzip.GzipFile(fileobj=r) as gz, open(tmp, "wb") as out:
        shutil.copyfileobj(gz, out)
    os.replace(tmp, db_path)
    marker.write_text(version)
    return "updated"
