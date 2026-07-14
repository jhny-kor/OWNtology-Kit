#!/usr/bin/env python3
"""Safari iCloud 탭 → source/safari-tabs/raw/ exporter.

iCloud 탭 동기화 DB(CloudTabs.db)를 read-only로 읽어 기기별(iPhone/iPad/Mac)
열린 탭 목록을 Markdown으로 저장한다. build_link_nodes.py가 이 폴더의 md에서
링크를 추출해 knowledge/links/nodes/ 정본 노드를 만든다.

터미널에 Full Disk Access 권한 필요 (시스템 설정 > 개인정보 보호 및 보안).

Usage:
    python3 collectors/safari_tabs_export.py
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path

VAULT = vault_path()
OUT_DIR = VAULT / "source" / "safari-tabs" / "raw"
DB = Path("~/Library/Containers/com.apple.Safari/Data/Library/Safari/CloudTabs.db").expanduser()


def _slug(name: str) -> str:
    return re.sub(r"[^\w가-힣-]+", "-", name).strip("-") or "device"


def main() -> int:
    if not DB.exists():
        print(f"[safari-tabs] ERROR: CloudTabs.db 없음: {DB}", file=sys.stderr)
        return 2
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro&immutable=1", uri=True)
        rows = con.execute(
            "SELECT COALESCE(d.device_name, t.device_uuid), t.title, t.url "
            "FROM cloud_tabs t LEFT JOIN cloud_tab_devices d "
            "ON d.device_uuid = t.device_uuid ORDER BY 1, t.position"
        ).fetchall()
        con.close()
    except sqlite3.Error as e:
        print(f"[safari-tabs] ERROR: DB 읽기 실패(Full Disk Access 권한?): {e}", file=sys.stderr)
        return 3

    by_device: dict[str, list[tuple[str, str]]] = {}
    for device, title, url in rows:
        if not url:
            continue
        by_device.setdefault(device or "unknown", []).append(((title or "").strip(), url.strip()))

    if not by_device:
        print("[safari-tabs] 열린 탭 없음")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    written = 0
    for device, tabs in by_device.items():
        fname = f"{today}-{_slug(device)}-safari-tabs.md"
        lines = [
            "---",
            f'title: "Safari 열린 탭 — {device} ({today})"',
            "type: link_group",
            f"date: {today}",
            f'summary: "{device} Safari에 열려 있던 탭 {len(tabs)}개"',
            "topics: [Safari, 링크모음]",
            "source: safari-icloud-tabs",
            "sensitivity: private",
            "---",
            "",
            f"# Safari 열린 탭 — {device} ({today})",
            "",
        ]
        for title, url in tabs:
            label = title.replace("[", "(").replace("]", ")") or url
            lines.append(f"- [{label}]({url})")
        (OUT_DIR / fname).write_text("\n".join(lines) + "\n", encoding="utf-8")
        written += 1
        print(f"[safari-tabs] {fname}: {len(tabs)} tabs")

    print(f"[safari-tabs] done: {written} device file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
