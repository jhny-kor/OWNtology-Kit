"""owntology-kit config — config.json 로드/저장 + 볼트 경로 해석.

모든 수집기/파이프라인 스크립트는 이 모듈로 볼트 경로를 얻는다.
우선순위: OWNTOLOGY_VAULT 환경변수 > config.json vault_path > 기본값.
vault_path()는 환경변수도 채워 넣어, 이후 서브프로세스/vault.py 임포트가
같은 볼트를 보도록 보장한다.
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

KIT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = KIT_ROOT / "config.json"

DEFAULTS: dict = {
    "vault_path": "~/Documents/my-owntology",
    # 사용자 직접입력 필드 — 비워두면 웹 화면(kit.py web)에서 입력
    "me": {"kakao_nickname": "", "name": "", "entity_slug": ""},
    # 공개 배포 안전 기본값: 전부 꺼진 상태로 시작 — 사용자가 웹 설정 화면에서
    # 수집할 소스를 명시적으로 켜야 한다(암묵적 대량 수집 방지).
    "sources": {"kakao": False, "sms": False, "mail": False, "notes": False,
                "safari_tabs": False, "github_stars": False},
    # 온톨로지화 선택 단계 on/off (핵심 단계는 항상 실행). 웹 설정 화면에서 편집.
    "pipeline": {"member_stubs": True, "link_nodes": True,
                 "personal_layer": True, "daily_rollup": True},
    "kakao": {"min_messages": 1, "include_services": False,
              "self_chat_id": "", "room_names": {}, "chat_aliases": {},
              # 수집에서 제외할 방(이름 또는 chat_id) — 민감한 대화 제외용
              "exclude_rooms": []},
    "sms": {"limit": 500},
    "mail": {"days": 14, "limit": 300},
    "github": {"username": "", "user_context": ""},
    "links": {
        "exclude_domains": [],
        "exclude_urls": {"github": [], "kakao": [], "other": []},
    },
    # 클라우드 동기화 (선택) — rsync 대상. SSH 키 인증이 미리 설정돼 있어야 함(비밀번호 저장 안 함).
    # 예: "user@server:/home/user/owntology-vault/"  · 방향은 로컬 → 원격(단방향 push).
    "sync": {"remote": ""},
    # 이름 → entity slug 수동 지정 (기본은 이름 그대로 사용)
    "entity_slugs": {},
}

LINK_SOURCES = {"github", "kakao", "other"}
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def normalize_link_url(value: str) -> str:
    value = str(value or "").strip().rstrip(".,;")
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    result = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"
    if parsed.params:
        result += f";{parsed.params}"
    if parsed.query:
        result += f"?{parsed.query}"
    if parsed.fragment:
        result += f"#{parsed.fragment}"
    return result


def normalize_link_domain(value: str) -> str:
    raw = str(value or "").strip().lower()
    if "://" in raw:
        try:
            raw = urlparse(raw).netloc
        except ValueError:
            return ""
    raw = raw.split("/", 1)[0].split(":", 1)[0].strip().removeprefix("*.").removeprefix("www.")
    return raw if _DOMAIN_RE.fullmatch(raw) else ""


def link_source_kind(source: str) -> str:
    return "other" if source in {"safari", "other"} else source


def is_link_excluded(url: str, source: str, settings: dict | None = None) -> bool:
    cfg = (settings or load()).get("links", {})
    normalized = normalize_link_url(url)
    if not normalized:
        return False
    source = link_source_kind(str(source))
    exact = {
        normalize_link_url(item)
        for item in (cfg.get("exclude_urls", {}).get(source, []) or [])
    }
    if normalized in exact:
        return True
    try:
        host = urlparse(normalized).hostname or ""
    except ValueError:
        return False
    host = host.lower().removeprefix("www.")
    domains = {
        normalize_link_domain(item)
        for item in (cfg.get("exclude_domains", []) or [])
    }
    return any(host == domain or host.endswith("." + domain) for domain in domains if domain)


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    if CONFIG_FILE.exists():
        try:
            return _merge(DEFAULTS, json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return copy.deepcopy(DEFAULTS)


def save(cfg: dict) -> None:
    CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def vault_path() -> Path:
    env = os.getenv("OWNTOLOGY_VAULT")
    if env:
        return Path(env).expanduser()
    p = Path(load().get("vault_path") or DEFAULTS["vault_path"]).expanduser()
    os.environ["OWNTOLOGY_VAULT"] = str(p)
    return p
