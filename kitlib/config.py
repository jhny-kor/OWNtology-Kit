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
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = KIT_ROOT / "config.json"

DEFAULTS: dict = {
    "vault_path": "~/Documents/my-owntology",
    # 사용자 직접입력 필드 — 비워두면 웹 화면(kit.py web)에서 입력
    "me": {"kakao_nickname": "", "name": "", "entity_slug": ""},
    "sources": {"kakao": True, "sms": True, "mail": True, "notes": True,
                "safari_tabs": True, "github_stars": False},
    # 온톨로지화 선택 단계 on/off (핵심 단계는 항상 실행). 웹 설정 화면에서 편집.
    "pipeline": {"member_stubs": True, "link_nodes": True,
                 "personal_layer": True, "daily_rollup": True},
    "kakao": {"min_messages": 1, "include_services": False,
              "self_chat_id": "", "room_names": {}, "chat_aliases": {}},
    "sms": {"limit": 500},
    "mail": {"days": 14, "limit": 300},
    "github": {"username": "", "user_context": ""},
    # 이름 → entity slug 수동 지정 (기본은 이름 그대로 사용)
    "entity_slugs": {},
}


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
