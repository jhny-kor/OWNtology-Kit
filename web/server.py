#!/usr/bin/env python3
"""owntology-kit 웹 화면 — 설정 + 수동입력 필드 편집 (stdlib only).

127.0.0.1 전용 바인딩(외부 노출 없음). 실행: python3 kit.py web
  - 설정 탭: config.json 필드 편집
  - 사람 탭: people/*.md 의 수동필드(relationship·phone·aliases) 편집
  - 채팅방 탭: 카카오 방 이름 변경(config kakao.room_names)
"""
from __future__ import annotations

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
from kitlib import config as kitconfig

# PORT 환경변수 우선(프리뷰/autoPort 대응). 미지정 시 8765 고정.
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8765"))
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
INDEX = Path(__file__).with_name("index.html")


# ── frontmatter helpers ──────────────────────────────────────

def _parse_fm(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v.startswith("[") and v.endswith("]"):
            fm[k] = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",") if x.strip()]
        else:
            fm[k] = v
    return fm


def _set_fm_field(text: str, key: str, value: str) -> str:
    line = f"{key}: {value}"
    if re.search(rf"(?m)^{re.escape(key)}\s*:", text):
        return re.sub(rf"(?m)^{re.escape(key)}\s*:.*$", line, text, count=1)
    end = text.find("\n---", 4)
    if end == -1:
        return text
    return text[:end] + "\n" + line + text[end:]


# ── API handlers ─────────────────────────────────────────────

def get_people() -> list[dict]:
    vault = kitconfig.vault_path()
    out = []
    people = vault / "people"
    if not people.exists():
        return out
    for fp in sorted(people.glob("*.md")):
        fm = _parse_fm(fp.read_text(encoding="utf-8", errors="ignore"))
        aliases = fm.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases] if aliases else []
        out.append({
            "path": f"people/{fp.name}",
            "name": fm.get("name") or fm.get("title") or fp.stem,
            "type": fm.get("type", "person"),
            "entity_id": fm.get("entity_id", ""),
            "relationship": fm.get("relationship", ""),
            "phone": fm.get("phone", ""),
            "aliases": aliases,
            "extraction": fm.get("extraction", ""),
        })
    # 수동입력이 비어 있는(자동생성) 사람 우선
    out.sort(key=lambda p: (bool(p["relationship"]) and bool(p["phone"]), p["name"]))
    return out


def save_person(data: dict) -> dict:
    vault = kitconfig.vault_path()
    rel = data.get("path", "")
    if not re.fullmatch(r"people/[^/]+\.md", rel):
        raise ValueError(f"허용되지 않는 경로: {rel}")
    fp = vault / rel
    if not fp.exists():
        raise FileNotFoundError(rel)
    text = fp.read_text(encoding="utf-8")
    for key in ("relationship", "phone"):
        if key in data:
            val = str(data[key]).strip()
            text = _set_fm_field(text, key, json.dumps(val, ensure_ascii=False))
    if "aliases" in data:
        aliases = [a.strip() for a in data["aliases"] if str(a).strip()]
        text = _set_fm_field(text, "aliases", json.dumps(aliases, ensure_ascii=False))
    # 사용자가 직접 확인/입력했음을 표시
    text = _set_fm_field(text, "extraction", "confirmed")
    fp.write_text(text, encoding="utf-8")
    return {"saved": rel}


def get_rooms() -> list[dict]:
    """카카오 방 목록: collector가 기록한 적용 이름(.room_names_applied.json) 기준."""
    vault = kitconfig.vault_path()
    cfg = kitconfig.load()
    overrides = cfg.get("kakao", {}).get("room_names") or {}
    applied_file = vault / "source" / "kakao" / ".room_names_applied.json"
    applied = {}
    if applied_file.exists():
        try:
            applied = json.loads(applied_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    rooms = [{"chat_id": cid, "name": name, "override": overrides.get(cid, "")}
             for cid, name in sorted(applied.items(), key=lambda x: x[1])]
    return rooms


def save_rooms(data: dict) -> dict:
    cfg = kitconfig.load()
    room_names = cfg.setdefault("kakao", {}).setdefault("room_names", {})
    for cid, name in (data.get("room_names") or {}).items():
        cid = str(cid).strip()
        name = str(name).strip()
        if not cid:
            continue
        if name:
            room_names[cid] = name
        else:
            room_names.pop(cid, None)
    kitconfig.save(cfg)
    return {"saved": len(room_names)}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/config":
                self._json(kitconfig.load())
            elif self.path == "/api/people":
                self._json(get_people())
            elif self.path == "/api/rooms":
                self._json(get_rooms())
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/config":
                kitconfig.save(kitconfig._merge(kitconfig.load(), data))
                self._json({"saved": True})
            elif self.path == "/api/people":
                self._json(save_person(data))
            elif self.path == "/api/rooms":
                self._json(save_rooms(data))
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 400)

    def log_message(self, fmt, *args):  # 조용히
        pass


def main() -> int:
    # 웹 API는 인증이 없고 볼트를 읽고/쓴다 — 루프백 외 바인딩은 거부(개인정보 노출 방지).
    # 원격 접근이 꼭 필요하면 SSH 터널/리버스 프록시로 앞단에서 인증을 두고 노출하라.
    if HOST not in _LOOPBACK:
        print(f"거부: HOST={HOST!r} 는 루프백이 아닙니다. 이 웹 화면은 인증이 없어 "
              f"127.0.0.1(localhost)에만 바인딩합니다. 원격 접근은 SSH 터널을 사용하세요.",
              file=sys.stderr)
        return 2
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"owntology-kit 웹 화면: http://{HOST}:{PORT}  (종료: Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
