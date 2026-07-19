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
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
from kitlib import config as kitconfig
from pipeline.build_link_nodes import (
    BARE_URL_RE,
    MARKDOWN_LINK_RE,
    LinkNode,
    date_from_path,
    domain_for,
    filename_for,
    normalize_url,
)


# ── 백그라운드 작업(수집·동기화) 실행 + 라이브 로그 ────────────
# 한 번에 하나만 실행. 프런트가 /api/job 을 폴링해 진행 로그를 본다.
_JOB = {"running": False, "kind": "", "log": [], "code": None}
_JOB_LOCK = threading.Lock()
_LINK_CACHE: dict[str, tuple[float, list[dict]]] = {}
_LINK_CACHE_LOCK = threading.Lock()


def _run_job(kind: str, cmd: list[str]) -> None:
    def worker():
        env = {**os.environ, "OWNTOLOGY_VAULT": str(kitconfig.vault_path())}
        proc = subprocess.Popen(cmd, cwd=str(KIT), env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                bufsize=1)
        for line in proc.stdout:
            with _JOB_LOCK:
                _JOB["log"].append(line.rstrip("\n"))
                if len(_JOB["log"]) > 500:
                    _JOB["log"] = _JOB["log"][-500:]
        proc.wait()
        with _JOB_LOCK:
            _JOB["running"] = False
            _JOB["code"] = proc.returncode
    with _JOB_LOCK:
        if _JOB["running"]:
            return
        _JOB.update(running=True, kind=kind, log=[f"$ {' '.join(cmd)}"], code=None)
    threading.Thread(target=worker, daemon=True).start()


def start_run() -> dict:
    _run_job("run", [sys.executable, str(KIT / "kit.py"), "run"])
    return {"started": "run"}


def start_sync() -> dict:
    remote = (kitconfig.load().get("sync", {}).get("remote") or "").strip()
    if not remote:
        raise ValueError("클라우드 동기화 대상(sync.remote) 미설정 — 설정 탭에서 입력하세요")
    vault = str(kitconfig.vault_path()).rstrip("/") + "/"
    _run_job("sync", ["rsync", "-az", "--delete", vault, remote])
    return {"started": "sync"}


def select_vault_folder() -> dict:
    if sys.platform != "darwin":
        raise RuntimeError("맥 폴더 선택은 macOS에서만 사용할 수 있습니다")
    try:
        proc = subprocess.run(
            ["osascript", "-e", 'POSIX path of (choose folder with prompt "볼트 폴더 선택")'],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError as e:
        raise RuntimeError("macOS osascript를 찾을 수 없습니다") from e
    if proc.returncode != 0:
        error = (proc.stderr or proc.stdout or "").strip()
        if "user canceled" in error.lower() or "cancel" in error.lower():
            return {"cancelled": True}
        raise RuntimeError(error[:300] or "폴더 선택에 실패했습니다")
    path = Path(proc.stdout.strip()).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("선택한 경로가 폴더가 아닙니다")
    return {"path": str(path)}


def job_status() -> dict:
    with _JOB_LOCK:
        return dict(_JOB)

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


def _room_id(value) -> str:
    return str(value).strip().removeprefix("chat-")


def _load_applied_rooms(vault: Path) -> dict:
    applied_file = vault / "source" / "kakao" / ".room_names_applied.json"
    if not applied_file.exists():
        return {}
    try:
        data = json.loads(applied_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_rooms() -> list[dict]:
    """카카오 방 목록: collector가 기록한 적용 이름(.room_names_applied.json) 기준."""
    vault = kitconfig.vault_path()
    cfg = kitconfig.load()
    overrides = {
        _room_id(cid): name
        for cid, name in (cfg.get("kakao", {}).get("room_names") or {}).items()
    }
    excluded = {
        _room_id(x)
        for x in (cfg.get("kakao", {}).get("exclude_rooms") or [])
        if str(x).strip()
    }
    applied = {_room_id(cid): str(name) for cid, name in _load_applied_rooms(vault).items()}
    aliases = cfg.get("kakao", {}).get("chat_aliases") or {}
    aliases_by_name = {}
    for old, new in aliases.items():
        old, new = str(old).strip(), str(new).strip()
        if old and new and old != new:
            aliases_by_name.setdefault(new, set()).add(old)
    rooms = [{"chat_id": cid, "name": name, "override": overrides.get(cid, ""),
              "aliases": sorted(aliases_by_name.get(name, set())),
              "excluded": cid in excluded or name in excluded}
             for cid, name in sorted(applied.items(), key=lambda x: x[1])]
    return rooms


def get_room_messages(chat_id: str, limit: int = 5) -> dict:
    cid = _room_id(chat_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cid):
        raise ValueError("유효하지 않은 chat_id")
    room = next((r for r in get_rooms() if r["chat_id"] == cid), None)
    if room is None:
        raise ValueError("알 수 없는 chat_id")
    source = kitconfig.vault_path() / "source" / "kakao"
    candidates = [
        source / f"kmsg-katok-id-{cid}.json",
        source / f"kmsg-katok-{cid}.json",
    ]
    snapshot = next((path for path in candidates if path.is_file()), None)
    if snapshot is None:
        for path in sorted(source.glob("kmsg-*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(data.get("chat", "")).strip() == room["name"]:
                snapshot = path
                break
    if snapshot is None:
        return {"chat_id": cid, "name": room["name"], "messages": []}
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    messages = data.get("messages") or []
    count = max(1, min(int(limit), 20))
    return {"chat_id": cid, "name": room["name"], "messages": messages[-count:]}


def _clear_link_cache() -> None:
    with _LINK_CACHE_LOCK:
        _LINK_CACHE.clear()


def get_link_rules() -> dict:
    links = kitconfig.load().get("links", {})
    excluded_urls = links.get("exclude_urls", {}) or {}
    return {
        "exclude_domains": list(links.get("exclude_domains", []) or []),
        "exclude_urls": {
            source: list(excluded_urls.get(source, []) or [])
            for source in sorted(kitconfig.LINK_SOURCES)
        },
    }


def save_link_rules(data: dict) -> dict:
    raw_domains = data.get("exclude_domains", [])
    if isinstance(raw_domains, str):
        raw_domains = re.split(r"[\n,]+", raw_domains)
    domains = []
    invalid = []
    for value in raw_domains or []:
        raw = str(value).strip()
        if not raw:
            continue
        normalized = kitconfig.normalize_link_domain(raw)
        if normalized:
            if normalized not in domains:
                domains.append(normalized)
        else:
            invalid.append(raw)
    settings = kitconfig.load()
    settings.setdefault("links", {})["exclude_domains"] = domains
    kitconfig.save(settings)
    _clear_link_cache()
    return {"saved": True, "exclude_domains": domains, "invalid": invalid}


def save_link_exclusion(data: dict) -> dict:
    source = str(data.get("source") or "").strip()
    if source not in kitconfig.LINK_SOURCES:
        raise ValueError("알 수 없는 링크 출처")
    url = kitconfig.normalize_link_url(str(data.get("url") or ""))
    if not url:
        raise ValueError("유효하지 않은 링크 URL")
    settings = kitconfig.load()
    links = settings.setdefault("links", {})
    excluded_urls = links.setdefault("exclude_urls", {})
    values = {
        kitconfig.normalize_link_url(item)
        for item in (excluded_urls.get(source, []) or [])
    }
    excluded = bool(data.get("excluded", True))
    if excluded:
        values.add(url)
    else:
        values.discard(url)
    excluded_urls[source] = sorted(value for value in values if value)
    kitconfig.save(settings)
    _clear_link_cache()
    return {"saved": True, "source": source, "url": url, "excluded": excluded}


def _link_root(source: str) -> Path:
    vault = kitconfig.vault_path()
    if source == "github":
        return vault / "knowledge" / "github-stars" / "repos"
    if source in {"kakao", "other"}:
        return vault / "knowledge" / "links" / "nodes"
    raise ValueError("알 수 없는 링크 출처")


def _node_item(source: str, url: str, title: str, date: str = "", summary: str = "") -> dict:
    return {
        "id": f"{source}:{filename_for(LinkNode(url=url))}",
        "title": title or url,
        "url": url,
        "domain": domain_for(url),
        "date": date,
        "summary": " ".join(summary.split()),
        "needs_enrichment": not bool(summary.strip()),
    }


def _github_items() -> list[dict]:
    items = {}
    root = _link_root("github")
    if not root.exists():
        return []
    for path in root.glob("*.md"):
        if path.name == "README.md":
            continue
        fm = _parse_fm(path.read_text(encoding="utf-8", errors="ignore"))
        url = normalize_url(str(fm.get("url") or ""))
        if not url:
            continue
        candidate = {
            "id": f"github:{path.name}",
            "title": str(fm.get("title") or path.stem),
            "url": url,
            "domain": domain_for(url),
            "date": str(fm.get("date") or ""),
            "summary": " ".join(str(fm.get("summary") or "").split()),
        }
        candidate["needs_enrichment"] = not bool(candidate["summary"])
        candidate["excluded"] = kitconfig.is_link_excluded(url, "github")
        current = items.get(url)
        if current is None or len(candidate["summary"]) > len(current["summary"]):
            items[url] = candidate
    return list(items.values())


def _kakao_items() -> list[dict]:
    nodes: dict[str, dict] = {}
    github_urls = {item["url"] for item in _link_items("github") if not item.get("excluded")}
    vault = kitconfig.vault_path()
    for pattern in ("kakao-links*.json", "kakao-*-links*.json"):
        for path in vault.joinpath("ontology").glob(pattern):
            if path.name.endswith(".tmp.json"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            links = data.get("links") if isinstance(data, dict) else None
            if not isinstance(links, list):
                continue
            for link in links:
                if not isinstance(link, dict):
                    continue
                url = normalize_url(str(link.get("url") or ""))
                if not url or url in github_urls:
                    continue
                title = " ".join(str(link.get("title") or url).split())
                date = str(link.get("date") or "")
                summary = str(link.get("summary") or "")
                item = nodes.setdefault(url, _node_item("kakao", url, title, date, summary))
                item["excluded"] = kitconfig.is_link_excluded(url, "kakao")
                if len(title) > len(item["title"]):
                    item["title"] = title
                if date and (not item["date"] or date < item["date"]):
                    item["date"] = date
                if summary and not item["summary"]:
                    item["summary"] = " ".join(summary.split())
                    item["needs_enrichment"] = False
    return list(nodes.values())


def _other_items() -> list[dict]:
    nodes: dict[str, dict] = {}
    excluded = {item["url"] for item in _link_items("github") if not item.get("excluded")}
    excluded.update(item["url"] for item in _link_items("kakao") if not item.get("excluded"))
    root = kitconfig.vault_path() / "source" / "safari-tabs"
    if not root.exists():
        return []
    for path in root.rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        date = date_from_path(path)
        seen_spans = set()
        for match in MARKDOWN_LINK_RE.finditer(text):
            seen_spans.add(match.span(2))
            url = normalize_url(match.group(2))
            if url and url not in excluded:
                item = nodes.setdefault(url, _node_item("other", url, match.group(1), date))
                item["excluded"] = kitconfig.is_link_excluded(url, "other")
        for match in BARE_URL_RE.finditer(text):
            if any(start <= match.start() < end for start, end in seen_spans):
                continue
            url = normalize_url(match.group(0).rstrip(").,;"))
            if url and url not in excluded:
                item = nodes.setdefault(url, _node_item("other", url, domain_for(url), date))
                item["excluded"] = kitconfig.is_link_excluded(url, "other")
    return list(nodes.values())


def _link_items(source: str) -> list[dict]:
    now = time.monotonic()
    with _LINK_CACHE_LOCK:
        cached = _LINK_CACHE.get(source)
        if cached and now - cached[0] < 30:
            return cached[1]
    builders = {"github": _github_items, "kakao": _kakao_items, "other": _other_items}
    try:
        items = builders[source]()
    except KeyError as error:
        raise ValueError("알 수 없는 링크 출처") from error
    items.sort(key=lambda item: (item["date"], item["title"].casefold()), reverse=True)
    with _LINK_CACHE_LOCK:
        _LINK_CACHE[source] = (now, items)
    return items


def get_links(source: str = "github", query: str = "", page: int = 1, limit: int = 50,
              include_excluded: bool = False) -> dict:
    if source not in kitconfig.LINK_SOURCES:
        raise ValueError("알 수 없는 링크 출처")
    page = max(1, int(page))
    limit = max(1, min(int(limit), 100))
    query = str(query).strip().casefold()
    items = _link_items(source)
    items = [item for item in items if include_excluded or not item.get("excluded")]
    if query:
        items = [item for item in items if query in " ".join((
            item["title"], item["url"], item["domain"], item["summary"],
        )).casefold()]
    total = len(items)
    start = (page - 1) * limit
    return {"source": source, "query": query, "page": page, "limit": limit,
            "total": total, "items": items[start:start + limit]}


def _note_body(text: str) -> str:
    if not text.startswith("---"):
        return text.strip()
    end = text.find("\n---", 3)
    return text[end + 4:].strip() if end != -1 else text.strip()


def _link_has_source(fm: dict, source: str) -> bool:
    if source == "github":
        return True
    sources = fm.get("sources") or []
    if isinstance(sources, str):
        sources = [sources]
    if source == "kakao":
        return "kakao" in sources
    return "safari" in sources or not sources


def get_link_detail(link_id: str) -> dict:
    source, separator, filename = str(link_id).partition(":")
    if source not in kitconfig.LINK_SOURCES or not separator or Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("유효하지 않은 링크 ID")
    path = _link_root(source) / filename
    if not path.is_file():
        raise FileNotFoundError(filename)
    text = path.read_text(encoding="utf-8", errors="ignore")
    fm = _parse_fm(text)
    if not _link_has_source(fm, source):
        raise ValueError("링크 출처가 일치하지 않습니다")
    metadata_keys = ("date", "domain", "sources", "source_count", "language", "github_stars",
                     "topics", "카테고리", "학습상태", "enriched")
    return {"id": link_id, "source": source,
            "title": str(fm.get("title") or path.stem), "url": str(fm.get("url") or ""),
            "summary": str(fm.get("summary") or ""),
            "metadata": {key: fm[key] for key in metadata_keys if fm.get(key) not in (None, "", [])},
            "content": _note_body(text)}


def delete_link(data: dict) -> dict:
    source, separator, filename = str(data.get("id") or "").partition(":")
    if source not in kitconfig.LINK_SOURCES or not separator or Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("유효하지 않은 링크 ID")
    path = _link_root(source) / filename
    if not path.is_file():
        raise FileNotFoundError(filename)
    text = path.read_text(encoding="utf-8", errors="ignore")
    fm = _parse_fm(text)
    url = kitconfig.normalize_link_url(str(fm.get("url") or ""))
    if not url:
        raise ValueError("링크 URL이 없는 노트는 삭제할 수 없습니다")
    save_link_exclusion({"source": source, "url": url, "excluded": True})
    removed = False
    if source == "github":
        path.unlink()
        removed = True
    else:
        raw_sources = fm.get("sources") or []
        if isinstance(raw_sources, str):
            raw_sources = [raw_sources]
        node_sources = {kitconfig.link_source_kind(str(item)) for item in raw_sources}
        if not node_sources or node_sources == {source}:
            path.unlink()
            removed = True
    _clear_link_cache()
    return {"deleted": removed, "excluded": True, "source": source, "url": url}


def save_rooms(data: dict) -> dict:
    vault = kitconfig.vault_path()
    cfg = kitconfig.load()
    kk = cfg.setdefault("kakao", {})
    room_names = {_room_id(cid): str(name).strip()
                  for cid, name in (kk.get("room_names") or {}).items()
                  if str(name).strip()}
    kk["room_names"] = room_names
    chat_aliases = kk.get("chat_aliases")
    if not isinstance(chat_aliases, dict):
        chat_aliases = {}
        kk["chat_aliases"] = chat_aliases
    applied = {_room_id(cid): str(name) for cid, name in _load_applied_rooms(vault).items()}
    for cid, name in (data.get("room_names") or {}).items():
        cid = _room_id(cid)
        name = str(name).strip()
        if not cid:
            continue
        if name:
            previous = applied.get(cid, "").strip()
            room_names[cid] = name
            if previous and previous != name:
                for old, target in list(chat_aliases.items()):
                    if str(target).strip() == previous:
                        chat_aliases[str(old).strip()] = name
                chat_aliases[previous] = name
        else:
            room_names.pop(cid, None)
    # 수집 제외 방(chat_id 기준) — 웹에서 체크한 방만 반영
    if "exclude_rooms" in data:
        known_ids = set(applied)
        known_names = set(applied.values())
        existing = [str(c).strip() for c in (kk.get("exclude_rooms") or []) if str(c).strip()]
        preserved = [c for c in existing if _room_id(c) not in known_ids and c not in known_names]
        submitted = [_room_id(c) for c in data["exclude_rooms"] if str(c).strip()]
        kk["exclude_rooms"] = list(dict.fromkeys(preserved + submitted))
    kitconfig.save(cfg)
    return {"saved": len(room_names), "excluded": len(kk.get("exclude_rooms", []))}


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
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            elif parsed.path == "/api/config":
                self._json(kitconfig.load())
            elif parsed.path == "/api/people":
                self._json(get_people())
            elif parsed.path == "/api/rooms":
                self._json(get_rooms())
            elif parsed.path == "/api/room-messages":
                params = parse_qs(parsed.query)
                self._json(get_room_messages(
                    params.get("chat_id", [""])[0],
                    int(params.get("limit", [5])[0]),
                ))
            elif parsed.path == "/api/links":
                params = parse_qs(parsed.query)
                self._json(get_links(
                    params.get("source", ["github"])[0],
                    params.get("q", [""])[0],
                    int(params.get("page", [1])[0]),
                    int(params.get("limit", [50])[0]),
                    params.get("include_excluded", ["0"])[0].lower() in {"1", "true", "yes", "on"},
                ))
            elif parsed.path == "/api/link-rules":
                self._json(get_link_rules())
            elif parsed.path == "/api/link":
                params = parse_qs(parsed.query)
                self._json(get_link_detail(params.get("id", [""])[0]))
            elif parsed.path == "/api/job":
                self._json(job_status())
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/config":
                merged = kitconfig._merge(kitconfig.load(), data)
                kitconfig.save(merged)
                if "vault_path" in data:
                    vault_path = str(merged.get("vault_path") or "").strip()
                    if vault_path:
                        os.environ["OWNTOLOGY_VAULT"] = str(Path(vault_path).expanduser())
                _clear_link_cache()
                self._json({"saved": True})
            elif self.path == "/api/people":
                self._json(save_person(data))
            elif self.path == "/api/rooms":
                self._json(save_rooms(data))
            elif self.path == "/api/link-rules":
                self._json(save_link_rules(data))
            elif self.path == "/api/link-exclusion":
                self._json(save_link_exclusion(data))
            elif self.path == "/api/link-delete":
                self._json(delete_link(data))
            elif self.path == "/api/run":
                self._json(start_run())
            elif self.path == "/api/sync":
                self._json(start_sync())
            elif self.path == "/api/select-folder":
                self._json(select_vault_folder())
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
