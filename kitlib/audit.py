"""
owntology 감사 로그 (위원회 B2) — MCP/REST 호출 기록.

원칙:
  - 모든 호출의 메타데이터를 JSONL 한 줄로 append (logs/audit.log, 볼트 밖).
  - 결과 본문은 기록하지 않는다 — 길이 + 플래그(blocked/masked)만. 로그가
    민감데이터 유출원이 되지 않게.
  - 감사 실패가 기능을 막지 않게 모든 예외를 삼킨다.
"""
import os
import json
import functools
import hashlib
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(os.getenv("OWNTOLOGY_LOG_DIR", str(Path(__file__).resolve().parent / "logs")))
LOG_FILE = LOG_DIR / "audit.log"
_PARAM_MAX = 300


def _result_summary(result):
    if result is None:
        return None
    s = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    flags = []
    if any(t in s for t in ("🔒", "_blocked", "\"blocked\": true", "접근 차단", "차단됨")):
        flags.append("blocked")
    if any(t in s for t in ("민감/원문 폴더", "source/email", "source/kakao", "source/sms")):
        flags.append("raw_access_denied")
    if "redacted_fields" in s:
        flags.append("field_redacted")
    if "redacted_categories" in s:
        flags.append("relation_redacted")
    if "[MASKED" in s:
        flags.append("masked")
    return {"len": len(s), "flags": flags}


def _safe_value(value):
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        digest = hashlib.sha256(value.encode()).hexdigest()[:12]
        return {"type": "str", "len": len(value), "sha256": digest}
    if isinstance(value, (list, tuple)):
        return {"type": "list", "len": len(value)}
    if isinstance(value, dict):
        return {str(k): _safe_value(v) for k, v in value.items()}
    text = str(value)
    return {"type": type(value).__name__, "len": len(text)}


def _trunc_params(params):
    if params is None:
        return None
    try:
        s = json.dumps(_safe_value(params), ensure_ascii=False, default=str)
    except Exception:
        s = '{"redacted": true}'
    return s[:_PARAM_MAX]


def log(channel: str, action: str, params=None, result=None, extra: dict = None):
    """감사 항목 1줄 기록."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "ch": channel,
            "action": action,
            "params": _trunc_params(params),
            "result": _result_summary(result),
        }
        if extra:
            safe_extra = dict(extra)
            if "error" in safe_extra:
                safe_extra["error"] = _safe_value(safe_extra["error"])
            entry.update(safe_extra)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass  # 감사 실패는 무시 (기능 우선)


# 개인 데이터를 반환하는 도구 — 모자이크 누적 민감도 추적 대상
_DATA_TOOLS = {
    "get_note", "search_notes", "list_notes", "get_people", "get_projects",
    "get_recent_conversations", "get_kakao_messages", "search_kakao_messages",
    "summarize_kakao_person", "list_kakao_members", "resolve_kakao_person_alias",
    "find_kakao_projects", "get_kakao_upcoming_tasks", "get_entity_relations",
    "find_conversations_about",
}


def audited(channel: str = "mcp"):
    """MCP 도구 함수를 감싸 호출 기록 + 모자이크 누적 민감도 검사.
    functools.wraps로 시그니처를 보존해 FastMCP 스키마 생성에 영향 없음."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            params = dict(kwargs)
            if args:
                params["_args"] = list(args)
            params = params or None
            # owner/public 티어를 매 호출 기록(5차 운영지표 — 티어별 호출·차단 집계용)
            try:
                import security
                tier = {"owner": security.owner_active()}
            except Exception:
                tier = {}
            try:
                r = fn(*args, **kwargs)
                extra = dict(tier)
                # 모자이크 효과: 데이터 도구의 결과 범주를 누적, 임계 초과 시 경고 주입
                if fn.__name__ in _DATA_TOOLS and isinstance(r, str):
                    try:
                        import security
                        cats = security.sensitive_categories(r)
                        high, distinct = security.record_and_check(cats)
                        if cats or high:
                            extra.update({"sens_cats": cats, "mosaic_window": distinct})
                        if high:
                            extra["mosaic_alert"] = True
                            r = security.mosaic_warning(distinct) + r
                    except Exception:
                        pass
                log(channel, fn.__name__, params, r, extra or None)
                return r
            except Exception as e:
                log(channel, fn.__name__, params, None, {**tier, "error": str(e)[:200]})
                raise
        return wrapper
    return deco
