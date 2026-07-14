"""
owntology security policy — 조회/검색 접근통제 + 반환 텍스트 시크릿 마스킹

위원회 평가 P0 대응:
  - get_note 경로통제 + 폴더 허용목록
  - 기본 검색에서 원문/민감 폴더 제외
  - 반환 텍스트의 비밀번호/인증링크/API키/OTP/사설망 마스킹

vault.py / kakao.py / rest_api.py 에서 공용으로 import 한다.
"""

import re
import contextvars
from pathlib import Path
from typing import Optional


# ── owner 세션 컨텍스트 ───────────────────────────────────────
# 유효 Bearer 토큰을 제시한 요청은 "owner"로 표시된다(server.py 미들웨어가 설정).
# owner면 sensitivity=sensitive 큐레이션 노트(본인·가족·인물 등)를 읽을 수 있다.
# 토큰 없음=기존 public 티어(비민감만). raw source 폴더 차단은 owner여도 유지.
_owner_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "owntology_owner", default=False)


def set_owner(flag: bool):
    """owner 여부 설정. 리셋용 토큰 반환(미들웨어 finally에서 reset_owner에 전달)."""
    return _owner_ctx.set(bool(flag))


def reset_owner(token) -> None:
    try:
        _owner_ctx.reset(token)
    except Exception:
        pass


def owner_active() -> bool:
    return _owner_ctx.get()


# ── 폴더 정책 ────────────────────────────────────────────────
# get_note / 기본 검색에서 차단되는 민감/원문 폴더 (경로 prefix, '/' 구분)
BLOCKED_FOLDER_PREFIXES = (
    "source/email",
    "source/kakao",
    "source/sms",
    "quarantine",
    "accounts",
    # career는 여기 넣지 않는다 — ChatGPT가 이력서·자소서를 folder=career 명시 호출로
    # 읽고 수정하는 작업 폴더. 기본 검색 제외는 vault._DEFAULT_SKIP이 담당(source와 동일 패턴).
    "_merged",
    "_templates",
)

# sensitivity frontmatter 값이 이 집합이면 기본 차단 (allow_sensitive=True 시에만 허용)
BLOCKED_SENSITIVITY = {"sensitive", "secret"}


# ── 관계 API 티어 (위원회 4차 P0) ─────────────────────────────
# 파일 단위 차단(민감 노트 전문)과 별개로 관계 그래프가 우회 경로였다:
# get_entity_relations 가 가족·직장·그룹 소속을 인증 없이 구조화 반환했음.
# public 세션에 허용되는 관계 allowlist — 여기 없는 관계(가족·고용·소속·연애 등)는
# owner 세션에서만 반환된다. 새 관계 유형이 생겨도 기본은 비공개(안전한 기본값).
PUBLIC_RELATIONS = {
    "works_on", "contributed_by",
    "depends_on", "depended_on_by",
    "applies_to", "has_decision",
    "related_to", "uses",
    # 프로젝트 계보(5차 P3) — 프로젝트 간 관계라 개인정보 아님
    "replaced_by", "replaces", "part_of", "has_part",
}

# redacted_categories 표기용 관계→범주 분류 (allowlist 밖 관계에만 적용)
_RELATION_CATEGORY = (
    (re.compile(r"father_of|mother_of|child_of|sibling|spouse_of|family"), "family"),
    (re.compile(r"works_at|employs|employment"), "employment"),
    (re.compile(r"member_of|has_member"), "group"),
    (re.compile(r"relationship|dating|partner_of"), "relationship"),
    (re.compile(r"account|loan|salary|finance"), "finance"),
    (re.compile(r"diagnos|health|treats"), "health"),
)


def relation_category(relation: str) -> str:
    """관계명을 민감 범주로 분류. 미분류 비공개 관계는 'personal'."""
    for pat, cat in _RELATION_CATEGORY:
        if pat.search(relation or ""):
            return cat
    return "personal"


def _norm(rel_path: str) -> str:
    return str(rel_path).replace("\\", "/").lstrip("./").lower()


def is_folder_blocked(rel_path: str) -> bool:
    """볼트 상대경로가 차단 폴더에 속하면 True."""
    p = _norm(rel_path)
    return any(p == pre or p.startswith(pre + "/") for pre in BLOCKED_FOLDER_PREFIXES)


def is_sensitivity_blocked(sensitivity: Optional[str], honor_owner: bool = True) -> bool:
    """sensitivity 값이 차단 대상이면 True.
    honor_owner=True(기본)면 owner 세션에서는 차단 해제(읽기 경로).
    쓰기/enrich 경로는 honor_owner=False로 호출해 owner여도 민감 노트를 건드리지 않는다."""
    if honor_owner and owner_active():
        return False
    return (sensitivity or "").strip().lower() in BLOCKED_SENSITIVITY


def resolve_in_vault(vault_path: Path, rel_path: str) -> Optional[Path]:
    """
    rel_path 를 vault 내부 절대경로로 안전 해석. 경로이탈(`..`/심볼릭/절대경로)이면 None.
    create_note(vault.py)의 가드 패턴을 조회 경로에도 동일 적용.
    """
    clean = str(rel_path).lstrip("/")
    if not clean.endswith(".md"):
        clean += ".md"
    try:
        candidate = (vault_path / clean).resolve()
        candidate.relative_to(vault_path.resolve())
    except (ValueError, OSError):
        return None
    return candidate


# ── 시크릿 마스킹 ────────────────────────────────────────────
# (정규식, 치환문자열) — 순서 중요(URL 먼저, 그다음 토큰/키, 코드, IP)
_REDACTIONS = [
    # 비밀번호 재설정 / 인증 / 로그인 / 초대 / 매직링크 URL 전체
    (re.compile(
        r"https?://[^\s)\]\"'<>]*"
        r"(?:reset|verify|verif|confirm|magic|invite|login|signin|sso|"
        r"oauth|token|auth|password|activate|onetime|one-time)"
        r"[^\s)\]\"'<>]*", re.IGNORECASE), "[MASKED_LINK]"),
    # URL 내 token/code/key 쿼리파라미터
    (re.compile(
        r"([?&](?:token|code|key|access_token|api_key|t|c|otp)=)[^&\s)\]\"'<>]+",
        re.IGNORECASE), r"\1[MASKED]"),
    # key/token/secret/password/인증키 : <value>  (bare key/token/pw 포함, standalone 키 패턴보다 먼저)
    (re.compile(
        r"(?i)\b(api[_\- ]?key|access[_\- ]?key|secret[_\- ]?key|client[_\- ]?secret|"
        r"key|secret|token|password|passwd|pwd|인증키|비밀키|비밀번호|시크릿)\b"
        r"\s*[:=]\s*[\"']?[^\s,;)\]\"'<>]{6,}"),
        lambda m: m.group(1) + ": [MASKED_KEY]"),
    # OpenAI/일반 API 키
    (re.compile(r"\bsk-[A-Za-z0-9._-]{12,}\b"), "[MASKED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[MASKED_KEY]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[MASKED_KEY]"),
    # Bearer 토큰
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer [MASKED_KEY]"),
    # OTP / 로그인 코드 (키워드 근처 4~8자리)
    (re.compile(
        r"(?i)(인증\s*(?:번호|코드)|로그인\s*코드|verification\s*code|one[\- ]?time\s*code|otp)"
        r"[^\d]{0,12}\b(\d{4,8})\b"),
        lambda m: m.group(0).replace(m.group(2), "[MASKED_CODE]")),
    # 사설망 IP (RFC1918) + .local
    (re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[MASKED_IP]"),
    (re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), "[MASKED_IP]"),
    (re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"), "[MASKED_IP]"),
    (re.compile(r"\b[A-Za-z0-9][A-Za-z0-9\-]{0,40}\.local\b"), "[MASKED_HOST]"),
]


def redact(text: Optional[str]) -> str:
    """반환 직전 텍스트에서 시크릿/민감 토큰을 [MASKED_*]로 치환."""
    if not text:
        return text or ""
    out = text
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    return out


# ── 모자이크 효과 방어 (위원회 재평가) ─────────────────────────
# 개별 노트가 안전해도 여러 민감 '범주'를 짧은 시간에 조합 조회하면 프로필이
# 재구성된다. 슬라이딩 윈도우로 누적 민감 범주를 추적해, 임계 초과 시 경고를 주입.
import os
import time
import json

_SENSITIVE_CATEGORIES = {
    "identity":     re.compile(r"(?i)주민(등록)?번호|생년월일|여권번호|실명|본명"),
    "finance":      re.compile(r"(?i)계좌|잔액|대출|이자|송금|입출금|연봉|급여|카드번호|결제금액|자산|투자금"),
    "health":       re.compile(r"(?i)병원|진단|처방|증상|우울|불안장애|질병|수술|진료|복용|정신과"),
    "relationship": re.compile(r"(?i)여자친구|남자친구|연애|연인|사귀|이별|썸|고백"),
    "location":     re.compile(r"(?i)집\s*주소|거주지|자취|본가|사는\s*곳|우리\s*집|[가-힣]+(시|구|동)\s*\d|\b10\.\d|\b192\.168\."),
    "employment":   re.compile(r"(?i)직장|회사|근무|퇴사|이직|연봉|입사|사내|works_at|employs"),
    "family":       re.compile(r"(?i)아버지|어머니|엄마|아빠|여동생|남동생|형|누나|가족|부모"
                               r"|father_of|mother_of|child_of|sibling|spouse_of"),
}

_MOSAIC_WINDOW_SEC = int(os.getenv("OWNTOLOGY_MOSAIC_WINDOW", "600"))   # 10분
_MOSAIC_THRESHOLD = int(os.getenv("OWNTOLOGY_MOSAIC_THRESHOLD", "4"))   # 서로 다른 범주 4개+
_MOSAIC_FILE = Path(os.getenv("OWNTOLOGY_LOG_DIR",
                              str(Path(__file__).resolve().parent / "logs"))) / "mosaic-budget.jsonl"


def sensitive_categories(text: Optional[str]) -> list:
    """텍스트에 등장하는 민감 범주 집합."""
    if not text:
        return []
    return sorted(c for c, pat in _SENSITIVE_CATEGORIES.items() if pat.search(text))


def record_and_check(categories) -> tuple:
    """접근 범주를 슬라이딩 윈도우에 기록하고 (위험여부, 윈도우내 누적범주) 반환.
    프로세스 3개(mcp/mcp-http/rest)가 공유하도록 파일 기반. 실패는 무시."""
    cats = list(categories or [])
    now = time.time()
    recent = []
    try:
        _MOSAIC_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _MOSAIC_FILE.exists():
            for line in _MOSAIC_FILE.read_text(encoding="utf-8").splitlines()[-500:]:
                try:
                    e = json.loads(line)
                    if now - e.get("t", 0) <= _MOSAIC_WINDOW_SEC:
                        recent.append(e)
                except Exception:
                    pass
        if cats:
            recent.append({"t": now, "c": cats})
            # 윈도우 내 기록만 다시 써서 파일이 무한히 커지지 않게
            _MOSAIC_FILE.write_text(
                "\n".join(json.dumps(e, ensure_ascii=False) for e in recent) + "\n",
                encoding="utf-8")
    except Exception:
        pass
    distinct = sorted({c for e in recent for c in e.get("c", [])})
    return (len(distinct) >= _MOSAIC_THRESHOLD, distinct)


def redact_names(text: Optional[str], names) -> str:
    """검색 결과/요약에서 제3자 실명·별칭을 [PERSON]으로 마스킹 (위원회 3차 P0:
    검색 summary로 인한 PII 간접노출 차단). names 는 길이 내림차순 정렬 권장."""
    if not text or not names:
        return text or ""
    out = text
    for nm in names:
        if nm and len(nm) >= 2 and nm in out:
            out = out.replace(nm, "[PERSON]")
    return out


def mosaic_warning(distinct) -> str:
    return ("⚠️ 모자이크 경고: 최근 " + ", ".join(distinct) +
            f" 등 {len(distinct)}개 민감 범주를 연속 조회했습니다. "
            "개별 항목은 비민감이어도 조합 시 프로필이 재구성될 수 있어 주의가 필요합니다.\n\n")
