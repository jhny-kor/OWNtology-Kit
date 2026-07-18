"""
owntology vault reader — frontmatter 파싱 + 텍스트 검색
"""

import fnmatch
import os, re, time
from pathlib import Path, PurePosixPath
from typing import Optional

import security

import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _kit_vault_path
VAULT_PATH = _kit_vault_path()

# 기본 검색은 블록리스트 방식 — 아래 차단 폴더만 빼고 큐레이션 폴더 전체를 순회한다.
# (화이트리스트는 새 폴더/하위 폴더가 검색 사각지대가 됨. 차단 대상만 명시해 유지)
# 명시 요청(folder=)으로만 접근 가능한 원문 폴더. 기본 검색·명시 모두에서 차단.
RAW_FOLDERS = {"source/email", "source/kakao", "source/sms", "source/safari-tabs", "source/safari-tabs/raw"}
EXCLUDE_DIRS = {
    "opencrab_data", "Users", ".obsidian",
    ".omc", ".omx", "_summaries", "__pycache__", "node_modules",
    # 민감/격리/스키마 폴더는 일반 순회에서 제외(P0-3)
    "quarantine", "accounts", "_templates", "_merged", "schemas",
}
# 기본 검색에서 제외할 상위 폴더: 노이즈/민감(EXCLUDE_DIRS) + 원문 source 전체.
# (quarantine=중복/실패/secrets, accounts=계정정보, source=원문 임포트 → 검색 비노출)
# career = ChatGPT 이력서·자소서 작업 폴더: 기본 검색엔 안 섞이고
# folder=career 명시 검색·get_note·update_note로만 접근(source와 같은 패턴, 쓰기는 허용).
_DEFAULT_SKIP = EXCLUDE_DIRS | {"source", "career"}
# 기본 검색에서 제외할 하위 경로: 외부 지식 덤프(링크 노드 ~2.3만 + GitHub 스타).
# 개인 질의 검색을 오염시키는 주범이라 career 패턴처럼 기본 비노출.
# folder="knowledge/links" 명시 요청으로는 그대로 접근 가능(쓰기·enrich 영향 없음).
_DEFAULT_SKIP_SUBPATHS = ("knowledge/links/", "knowledge/github-stars/")
_OKIGNORE_CACHE: tuple[float, list[tuple[str, bool]]] | None = None


def _okignore_patterns() -> list[tuple[str, bool]]:
    global _OKIGNORE_CACHE
    path = VAULT_PATH / ".okignore"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        if _OKIGNORE_CACHE is not None:
            _OKIGNORE_MEMO.clear()
        _OKIGNORE_CACHE = None
        return []
    if _OKIGNORE_CACHE and _OKIGNORE_CACHE[0] == mtime:
        return _OKIGNORE_CACHE[1]
    patterns: list[tuple[str, bool]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        include = line.startswith("!")
        if include:
            line = line[1:]
        patterns.append((line, include))
    _OKIGNORE_CACHE = (mtime, patterns)
    _OKIGNORE_MEMO.clear()  # 패턴이 바뀌면 경로별 판정 캐시도 무효화
    return patterns


def _matches_okignore(rel: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return rel.startswith(pattern)
    if "/" in pattern:
        return PurePosixPath(rel).match(pattern)
    return any(fnmatch.fnmatchcase(part, pattern) for part in rel.split("/"))


# 경로별 okignore 판정 메모 — 패턴 ~35개 × 25k 파일의 fnmatch/PurePosixPath.match가
# 쿼리당 ~7s였음. rel 경로는 불변이므로 패턴이 바뀔 때만(_okignore_patterns) 비운다.
_OKIGNORE_MEMO: dict[str, bool] = {}


def _is_okignored(rel: str) -> bool:
    hit = _OKIGNORE_MEMO.get(rel)
    if hit is not None:
        return hit
    ignored = False
    for pattern, include in _okignore_patterns():
        if _matches_okignore(rel, pattern):
            ignored = not include
    _OKIGNORE_MEMO[rel] = ignored
    return ignored


def _default_bases() -> list:
    """기본 검색(folder 미지정)이 순회할 상위 폴더 목록. 점-폴더와 _DEFAULT_SKIP 제외.
    owner 세션(item 7)에서는 career('내 경력')를 기본 검색에 포함 — 외부노출 방지가
    목적인 제외라 본인 인증 세션엔 불필요. source 등 원문은 owner여도 계속 제외."""
    if not VAULT_PATH.exists():
        return []
    skip = _DEFAULT_SKIP - {"career"} if security.owner_active() else _DEFAULT_SKIP
    return [d for d in sorted(VAULT_PATH.iterdir())
            if d.is_dir() and not d.name.startswith(".") and d.name not in skip]


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_raw = text[4:end]
    body = text[end + 4:]
    fm = {}
    for line in fm_raw.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v.startswith("[") and v.endswith("]"):
                inner = v[1:-1]
                fm[k] = [x.strip().strip('"').strip("'").strip("[[").strip("]]")
                          for x in inner.split(",") if x.strip()]
            else:
                fm[k] = v
    return fm, body


def _scalar(value, default: str = "") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, list):
        return " ".join(str(x) for x in value if x)
    return str(value)


_TOPIC_ALIASES = {
    "github star": "github-repository",
    "github stars": "github-repository",
    "github-star": "github-repository",
    "github-stars": "github-repository",
    # 표기 변형 정규화(위원회 지적) — 링크 임포트 등 향후 유입분도 읽기 시점에 통일
    "claude-code": "Claude Code",
    "claude code": "Claude Code",
    "ai-agent": "AI Agent",
    "ai-agents": "AI Agent",
    "ai agent": "AI Agent",
    "ai agents": "AI Agent",
    "온톨로지": "Ontology",
    # canonical topic ID(위원회 4차) — 표기 변형을 읽기 시점에 하나로 접는다
    "owntology": "Ontology",
    "ai도구": "AI 도구",
    "ai tools": "AI 도구",
    "ai tool": "AI 도구",
    "ai 에이전트": "AI Agent",
    "ai에이전트": "AI Agent",
    "에이전트": "AI Agent",
    "agent": "AI Agent",
    "agents": "AI Agent",
}
_TOPIC_DROP = {
    "other", "기타",
    "youtube", "twitter", "x", "news", "code",
    "site", "google", "korean_portal", "threads",
    "신규", "링크 카탈로그", "링크 배치", "링크 전수처리",
}


# canonical topic 매핑(5차 P4) — indexes/topic_canonical.json 의 {map, drop}을
# 읽기 시점에 적용해 유사/변형 토픽을 canonical 라벨로 접는다. 파일 frontmatter는
# 원문 그대로(비침습·가역), 통계·필터·검색은 canonical만 보게 된다.
_TOPIC_CANON_CACHE: tuple[float, dict, frozenset] | None = None


def _topic_canonical() -> tuple[dict, frozenset]:
    global _TOPIC_CANON_CACHE
    path = VAULT_PATH / "indexes" / "topic_canonical.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}, frozenset()
    if _TOPIC_CANON_CACHE and _TOPIC_CANON_CACHE[0] == mtime:
        return _TOPIC_CANON_CACHE[1], _TOPIC_CANON_CACHE[2]
    try:
        import json as _json_mod
        data = _json_mod.loads(path.read_text(encoding="utf-8"))
        cmap = {str(k).casefold(): str(v) for k, v in (data.get("map") or {}).items()}
        drop = frozenset(str(x).casefold() for x in (data.get("drop") or []))
    except Exception:
        cmap, drop = {}, frozenset()
    if _TOPIC_CANON_CACHE is not None and _TOPIC_CANON_CACHE[0] != mtime:
        _NOTE_CACHE.clear()  # 맵 갱신 시 캐시된 노트의 구 토픽도 무효화
    _TOPIC_CANON_CACHE = (mtime, cmap, drop)
    return cmap, drop


def _normalize_topics(value) -> list:
    raw = value if isinstance(value, list) else [value]
    cmap, cdrop = _topic_canonical()
    out = []
    seen = set()
    for item in raw:
        topic = str(item).strip().strip('"').strip("'")
        if not topic:
            continue
        key = topic.casefold()
        if key in _TOPIC_DROP or key in cdrop or re.match(r"^20\d{2}-\d{2}-\d{2}", topic):
            continue
        topic = _TOPIC_ALIASES.get(key, topic)
        topic = cmap.get(topic.casefold(), topic)
        folded = topic.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        out.append(topic)
    return out


def _note_from_file(fpath: Path, full: bool = True) -> dict:
    try:
        if full:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        else:
            # 검색/목록 경로: 본문은 미리보기(500자)·계층 판정(>2000)에만 쓰므로 앞 64KB만
            # 읽는다. 카카오 등 수 MB 로그를 쿼리마다 통째로 읽던 I/O 제거. 전문은 get_note(full=True).
            with fpath.open(encoding="utf-8", errors="ignore") as fh:
                text = fh.read(65536)
    except Exception:
        return {}
    fm, body = _parse_frontmatter(text)
    rel = str(fpath.relative_to(VAULT_PATH))
    return {
        "path": rel,
        "title": _scalar(fm.get("title") or fm.get("name"), fpath.stem).strip('"'),
        "url": _scalar(fm.get("url")),
        "type": _scalar(fm.get("type"), "note"),
        "date": _scalar(fm.get("date", fm.get("updated", ""))),
        "summary": _scalar(fm.get("summary")),
        "keywords": fm.get("keywords", []),
        "topics": _normalize_topics(fm.get("topics", [])),
        "entities": fm.get("entities", []),
        "people": fm.get("people", []),
        "project": _scalar(fm.get("project")).strip("[[]]\"'"),
        "sensitivity": _scalar(fm.get("sensitivity"), "private"),
        "source": _scalar(fm.get("source", fm.get("source_type", ""))),
        "tags": fm.get("tags", []),
        "status": _scalar(fm.get("status")),
        "release_status": _scalar(fm.get("release_status")),
        "tech": fm.get("tech", []) if isinstance(fm.get("tech", []), list) else [],
        "relationship": _scalar(fm.get("relationship")),
        "aliases": fm.get("aliases", []) if isinstance(fm.get("aliases", []), list) else [],
        "body_preview": body.strip()[:500],
        "_body": body,
        "_fm": fm,
    }


# 검색/목록 경로용 파싱 캐시(Layer1). 장수 프로세스(FastMCP/REST)에서 매 쿼리
# 전 노트를 재읽기·재파싱하던 비용을 제거 — mtime 변화 시에만 재파싱한다.
# get_note는 전문이 필요하므로 캐시를 거치지 않고 full 로 직접 읽는다.
# ponytail: GIL 단일키 연산이라 락 불필요(최악은 무해한 중복 파싱). 삭제된 파일
# 항목은 남지만 _iter_notes가 안 내보내 반환 안 됨 — 볼트 churn만큼만 누적, 무시 가능.
_NOTE_CACHE: dict[str, tuple[float, dict]] = {}


def _searchable_text(note: dict) -> str:
    """검색 대상 텍스트(별칭·이름 포함 — 한/영 별칭 매칭). 파일이 바뀔 때만 재계산해
    캐시에 실어둔다 — 쿼리마다 30k개 노트의 join+lower를 반복하던 비용 제거."""
    return " ".join([
        note["title"],
        str(note.get("_fm", {}).get("name", "")),
        note["summary"],
        " ".join(note["aliases"] if isinstance(note.get("aliases"), list) else []),
        " ".join(note["keywords"] if isinstance(note["keywords"], list) else []),
        " ".join(note["topics"] if isinstance(note["topics"], list) else []),
        " ".join(note["entities"] if isinstance(note["entities"], list) else []),
        note["project"],
        note["body_preview"],
    ]).lower()


def _cached_note(fpath: Path) -> dict:
    key = str(fpath)
    try:
        mtime = fpath.stat().st_mtime
    except OSError:
        return {}
    hit = _NOTE_CACHE.get(key)
    if hit and hit[0] == mtime:
        return dict(hit[1])  # 얕은 복사 — 호출부의 redact가 캐시를 오염시키지 않게
    note = _note_from_file(fpath, full=False)
    if note:
        note["_searchable"] = _searchable_text(note)
    _NOTE_CACHE[key] = (mtime, note)
    return dict(note)


def prewarm_cache():
    """서비스 시작 시 백그라운드로 _NOTE_CACHE를 채운다 — 재시작 직후 첫 검색이
    콜드 풀파싱(~15s)을 뒤집어쓰지 않게. 데몬 스레드라 프로세스 종료를 막지 않는다."""
    import threading

    def run():
        try:
            for fpath in _iter_notes():
                _cached_note(fpath)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def _walk_bases(bases, allow_raw: bool, skip_subpaths: tuple = ()):
    for base in bases:
        if not base.exists():
            continue
        for fpath in sorted(base.rglob("*.md")):
            # EXCLUDE_DIRS는 볼트 상대경로 기준으로 매칭한다.
            # (절대경로로 매칭하면 '/Users/...' 같은 호스트 경로의 'Users'에
            #  걸려 전 파일이 제외되는 버그가 있었음)
            rel_parts = fpath.relative_to(VAULT_PATH).parts
            if any(ex in rel_parts for ex in EXCLUDE_DIRS):
                continue
            rel = str(fpath.relative_to(VAULT_PATH))
            if skip_subpaths and rel.startswith(skip_subpaths):
                continue
            if _is_okignored(rel):
                continue
            if not allow_raw and security.is_folder_blocked(rel):
                continue
            yield fpath


# 기본 검색(folder=None)의 파일 목록 캐시 — rglob walk가 쿼리당 ~4s라 60초 재사용.
# ponytail: 외부 sync로 생긴 새 노트는 최대 60초 늦게 검색에 반영. 내부 쓰기
# (create_note/write_raw_md)는 즉시 무효화하므로 방금 쓴 노트는 바로 검색된다.
# 삭제된 파일은 _cached_note가 stat 실패로 {}를 반환해 호출부에서 걸러진다.
_FILELIST_TTL = 60.0
_FILELIST_CACHE: dict = {"t": 0.0, "files": None}


def _invalidate_filelist():
    _FILELIST_CACHE["files"] = None


def _iter_notes(folder: Optional[str] = None, allow_raw: bool = False):
    """
    노트 순회. folder 미지정 시 _default_bases()(블록리스트)만 순회한다.
    (source·quarantine·accounts·노이즈만 제외, 나머지 큐레이션 폴더는 전체 포함)
    allow_raw=False면 민감/원문 폴더는 건너뛴다.
    """
    if folder:
        # 명시 요청이라도 원문/민감 폴더는 allow_raw 없이는 차단
        if not allow_raw and (security.is_folder_blocked(folder)
                              or security._norm(folder) in {f.lower() for f in RAW_FOLDERS}):
            return
        yield from _walk_bases([VAULT_PATH / folder], allow_raw)
        return
    if allow_raw:
        yield from _walk_bases(_default_bases(), allow_raw, _DEFAULT_SKIP_SUBPATHS)
        return
    # owner 세션은 career를 포함하므로(item 7) 캐시를 owner 상태로 구분 —
    # 안 그러면 owner가 채운 career 목록이 이후 non-owner 요청에 새어나감(보안).
    owner = security.owner_active()
    files = _FILELIST_CACHE["files"]
    if (files is None or _FILELIST_CACHE.get("vault") != str(VAULT_PATH)
            or _FILELIST_CACHE.get("owner") != owner
            or time.time() - _FILELIST_CACHE["t"] > _FILELIST_TTL):
        files = list(_walk_bases(_default_bases(), allow_raw=False,
                                 skip_subpaths=_DEFAULT_SKIP_SUBPATHS))
        _FILELIST_CACHE.update({"t": time.time(), "files": files,
                                "vault": str(VAULT_PATH), "owner": owner})
    yield from files


def _slugify(text: str) -> str:
    import re
    return re.sub(r"[^\w가-힣-]", "-", text).strip("-")[:50]


# ── Public API ──────────────────────────────────────────────

# 검색 질의 라우터(위원회 4차 P1): 의도를 구조화 필드 조회로 변환한다.
# "출시한 앱"은 텍스트 유사도 검색이 아니라 release_status=released 조건 조회여야
# 한다 — 텍스트 매칭은 외부 링크·가이드 문서가 상위를 오염시킨다.
# (질의 가드 패턴, 대상 폴더, 노트 필터, 라우트 라벨). 프로젝트 라우트는
# 앱/프로젝트 계열 단어가 함께 있을 때만 발동해 일반 검색 하이재킹을 막는다.
_PROJECT_WORDS = re.compile(r"앱|프로젝트|서비스|프로그램|툴|봇|app|project")
_INTENT_ROUTES = (
    (re.compile(r"출시|발매|릴리스|런칭|배포한|released"), _PROJECT_WORDS, "projects",
     lambda n: n.get("release_status") == "released",
     "type=project release_status=released"),
    (re.compile(r"개발\s*중|만들고\s*있|진행\s*중|작업\s*중|developing"), _PROJECT_WORDS, "projects",
     lambda n: n.get("release_status") == "developing" and n.get("status") == "active",
     "type=project release_status=developing status=active"),
    (re.compile(r"완료(한|했던|된)\s*(일|이벤트|작업)|있었던\s*일"), None, "events",
     lambda n: True, "type=event"),
)


def _route_structured(query: str) -> tuple[list[dict], list[str]]:
    """의도 매칭 시 구조화 필터로 노트를 모아 (결과, 라우트 라벨) 반환. 미매칭 시 ([], [])."""
    q = query.lower()
    matched, labels, seen_paths = [], [], set()
    for pat, guard, folder, pred, label in _INTENT_ROUTES:
        if not pat.search(q) or (guard and not guard.search(q)):
            continue
        labels.append(label)
        for fpath in _iter_notes(folder):
            note = _cached_note(fpath)
            if not note or note["path"] in seen_paths:
                continue
            if security.is_sensitivity_blocked(note.get("sensitivity")):
                continue
            if pred(note):
                seen_paths.add(note["path"])
                note["_routed"] = label
                matched.append(note)
    matched.sort(key=lambda n: n.get("date", ""), reverse=True)
    return matched, labels


# 검색 지연 관측(위원회 운영 지표) — 최근 200회 롤링. 프로세스 로컬로 충분.
from collections import deque as _deque
_SEARCH_TIMES: "_deque[float]" = _deque(maxlen=200)


def search_notes(query: str, limit: int = 8, folder: Optional[str] = None,
                 allow_sensitive: bool = False) -> list[dict]:
    """쿼리 키워드로 노트 전문 검색. 민감도/원문 폴더는 기본 제외, 결과는 마스킹.
    질의가 구조화 의도('출시한 앱' 등)에 매칭되면 텍스트 유사도 대신 구조화 필드
    조회 결과를 반환한다(P1 라우터). folder 명시 시엔 라우터를 건너뛴다."""
    _t0 = time.time()
    try:
        if folder is None:
            routed, _labels = _route_structured(query)
            if routed:
                names = _third_party_names()
                for n in routed:
                    n["summary"] = security.redact_names(security.redact(n.get("summary", "")), names)
                    n["body_preview"] = security.redact_names(security.redact(n.get("body_preview", "")), names)
                    n["title"] = security.redact_names(n.get("title", ""), names)
                return routed[:limit]
        return _text_search(query, limit=limit, folder=folder,
                            allow_sensitive=allow_sensitive)
    finally:
        _SEARCH_TIMES.append(time.time() - _t0)


def _text_search(query: str, limit: int = 8, folder: Optional[str] = None,
                 allow_sensitive: bool = False) -> list[dict]:
    """텍스트 유사도 검색(라우터 미매칭 시 폴백)."""
    terms = query.lower().split()
    results = []

    for fpath in _iter_notes(folder):
        note = _cached_note(fpath)
        if not note:
            continue
        # sensitivity 게이트 (P0-3)
        if not allow_sensitive and security.is_sensitivity_blocked(note.get("sensitivity")):
            continue
        # 검색 대상 텍스트는 캐시에 프리컴퓨트됨(_searchable_text 참고)
        searchable = note.get("_searchable") or _searchable_text(note)

        score = sum(searchable.count(t) for t in terms)
        if score > 0:
            # 검색 계층(P3): tier 우선 정렬 → curated 엔티티가 항상 대화 위에.
            # 원문 대화(tier 5)는 정제 결과 다음의 fallback으로만 노출됨.
            results.append((_search_tier(note), score * _source_weight(note), note))

    # tier 오름차순(1=curated 먼저), 같은 tier 내에서는 점수 내림차순
    results.sort(key=lambda x: (x[0], -x[1]))
    # 동일 소스 중복 제거(P2): 같은 source_ids/제목을 가진 결과가 여러 개의
    # 독립 근거처럼 보이는 것을 방지. 최고 점수 1건만 남긴다.
    out = []
    seen_keys = set()
    names = _third_party_names()  # 검색 요약 PII 마스킹(P0)
    for _tier, _sc, n in results:
        key = _dedup_key(n)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        n["summary"] = security.redact_names(security.redact(n.get("summary", "")), names)
        n["body_preview"] = security.redact_names(security.redact(n.get("body_preview", "")), names)
        n["title"] = security.redact_names(n.get("title", ""), names)
        out.append(n)
        if len(out) >= limit:
            break
    return out


# 검색 계층(위원회 P3): curated 엔티티 > 확정 사실 > 프로젝트 > 대화요약 > 원문.
# 원문 대화는 정제 결과보다 절대 위로 오지 않도록 큰 격차를 둔다.
_FOLDER_WEIGHT = (
    ("people/", 2.0), ("organizations/", 2.0),     # 1순위: curated 엔티티
    ("decisions/", 1.8), ("events/", 1.8), ("preferences/", 1.7),  # 2순위: 확정 사실
    ("projects/", 1.7),                            # 3순위: 프로젝트
    ("knowledge/", 1.3), ("daily/", 1.1),
    ("conversations/", 1.0),                       # 4·5순위: 대화요약/원문
)


def _search_tier(note: dict) -> int:
    """검색 밴드(P3): 0=curated, 1=대화요약, 2=원문대화. 밴드 내에서는 가중점수로 경쟁.
    하드 계층(엔티티 종류별)은 정답 정제노트를 약매칭 엔티티가 밀어내는 부작용이 있어,
    'curated가 대화 위'라는 핵심만 밴드로 강제하고 세부 우열은 _source_weight에 맡긴다."""
    path = (note.get("path") or "").replace("\\", "/")
    if path.startswith(("people/", "organizations/", "projects/", "preferences/",
                        "decisions/", "events/", "knowledge/", "daily/")):
        return 0  # curated 밴드
    if path.startswith("conversations/"):
        body = note.get("_body") or note.get("body_preview") or ""
        return 2 if len(body) > 2000 else 1   # 원문(긺)=fallback, 요약=1
    return 1


def _source_weight(note: dict) -> float:
    path = (note.get("path") or "").replace("\\", "/")
    base = 1.0
    for prefix, w in _FOLDER_WEIGHT:
        if path.startswith(prefix):
            base = w
            break
    fm = note.get("_fm", {})
    # 사실 상태(P2) 반영: confirmed 우대, inferred/proposed 소폭 감점
    status = str(fm.get("status") or fm.get("extraction") or "").lower()
    if status == "confirmed":
        base *= 1.15
    elif status in ("inferred", "proposed", "auto"):
        base *= 0.95
    # 원문성 긴 대화는 요약보다 낮게(5순위 fallback 성격)
    if path.startswith("conversations/"):
        body = note.get("_body") or note.get("body_preview") or ""
        if len(body) > 2000:
            base *= 0.85
    return base


_THIRD_PARTY_NAMES = None


def _third_party_names() -> list:
    """제3자(본인 제외) 실명·별칭 집합 — 검색 요약 PII 마스킹용(P0). 길이 내림차순.
    people 엔티티의 name/aliases + group 노트의 평문 member 이름에서 수집, 본인 제외."""
    global _THIRD_PARTY_NAMES
    if _THIRD_PARTY_NAMES is not None:
        return _THIRD_PARTY_NAMES
    self_names, others = set(), set()
    pdir = VAULT_PATH / "people"
    if pdir.exists():
        for fp in pdir.rglob("*.md"):
            if any(p in ("_templates", "_merged") for p in fp.parts):
                continue
            fm = _note_from_file(fp).get("_fm", {})
            # 인물 노트만 이름/별칭을 PII로 수집(그룹 라벨 '프론티어' 등은 제외)
            if fm.get("type") == "person":
                cand = set()
                nm = fm.get("name")
                if isinstance(nm, str) and nm.strip():
                    cand.add(nm.strip().strip('"'))
                for a in (fm.get("aliases") or []):
                    if isinstance(a, str) and a.strip():
                        cand.add(a.strip())
                is_self = str(fm.get("is_self", "")).lower() in ("true", "1") \
                    or "self" in (fm.get("tags") or [])
                (self_names if is_self else others).update(cand)
            elif fm.get("type") == "group":
                # 그룹의 평문 구성원 이름만 수집(그룹 자체 이름은 제외)
                for m in (fm.get("members") or []):
                    if isinstance(m, str) and m.strip() and not m.startswith(("person:", "group:")):
                        others.add(m.strip())
    others -= self_names
    _THIRD_PARTY_NAMES = sorted({n for n in others if len(n) >= 2}, key=len, reverse=True)
    return _THIRD_PARTY_NAMES


def _dedup_key(note: dict):
    """동일 소스 판별 키: source_ids 우선, 없으면 정규화 제목."""
    sid = note.get("_fm", {}).get("source_ids")
    if isinstance(sid, list) and sid:
        return ("src", tuple(sorted(sid)))
    if isinstance(sid, str) and sid.strip():
        return ("src", sid.strip())
    title = (note.get("title") or "").strip().lower()
    return ("title", re.sub(r"\s+", " ", title))


def get_note(path: str, allow_sensitive: bool = False) -> Optional[dict]:
    """
    경로로 노트 전체 내용 반환. 경로이탈 가드 + 폴더/민감도 차단 + 시크릿 마스킹.
    (이전엔 `..` 가드도 없고 rglob 부분매칭으로 어떤 파일이든 조회됐음 — P0-2 수정)
    차단 시 {"_blocked": True, ...} 반환, 없으면 None.
    """
    fpath = security.resolve_in_vault(VAULT_PATH, path)
    if fpath is None or not fpath.exists():
        return None
    rel = str(fpath.relative_to(VAULT_PATH))
    if not allow_sensitive and security.is_folder_blocked(rel):
        return {"_blocked": True, "path": rel,
                "reason": "민감/원문 폴더(source/email·kakao·quarantine·accounts 등)는 기본 조회가 차단됩니다."}
    note = _note_from_file(fpath)
    if not allow_sensitive and security.is_sensitivity_blocked(note.get("sensitivity")):
        return {"_blocked": True, "path": rel,
                "reason": f"sensitivity={note.get('sensitivity')} 노트는 기본 조회가 차단됩니다."}
    note["body"] = security.redact(note.pop("_body", ""))
    note["summary"] = security.redact(note.get("summary", ""))
    note.pop("body_preview", None)
    return note


def list_notes(folder: str = "projects", limit: int = 20, offset: int = 0) -> list[dict]:
    """폴더 내 노트 목록 (메타데이터만). offset 으로 페이지네이션.
    민감 노트는 owner 세션이 아니면 목록에서도 제외한다(5차 P0 — 검색·get_note와 동일
    게이트. 이전엔 list_notes(people)가 민감 인물의 실명·관계 메타데이터를 우회 노출)."""
    notes = []
    skipped = 0
    for fpath in _iter_notes(folder):
        note = _cached_note(fpath)
        if not note:
            continue
        if security.is_sensitivity_blocked(note.get("sensitivity")):
            continue
        if skipped < offset:
            skipped += 1
            continue
        notes.append({k: v for k, v in note.items()
                      if k not in ("_body", "_fm", "body_preview", "_searchable")})
        if len(notes) >= limit:
            break
    return notes


def get_projects() -> list[dict]:
    """모든 프로젝트 목록."""
    return list_notes("projects", limit=50)


def get_people() -> list[dict]:
    """모든 people 노트. (list_notes의 민감도 게이트 적용 — 비-owner는 민감 인물 제외)"""
    return list_notes("people", limit=50)


def get_people_summary() -> dict:
    """public 티어용 인물 집계(5차 P0) — 실명·별칭·관계 없이 유형별 개수만."""
    counts: dict = {}
    for fpath in _iter_notes("people"):
        t = _cached_note(fpath).get("type") or "unknown"
        counts[t] = counts.get(t, 0) + 1
    return {"counts_by_type": counts,
            "redacted_fields": ["name", "aliases", "relationship"],
            "notice": "인물 실명·별칭·관계는 owner 인증(Bearer 토큰) 세션에서만 조회됩니다."}


def get_recent_conversations(days: int = 7) -> list[dict]:
    """최근 N일 대화 (요약 노드 포함)."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    results = []
    for fpath in _iter_notes("conversations"):
        note = _cached_note(fpath)
        if not note:
            continue
        if security.is_sensitivity_blocked(note.get("sensitivity")):
            continue
        date = note.get("date", "")
        if date >= cutoff:
            note["summary"] = security.redact(note.get("summary", ""))
            results.append({k: v for k, v in note.items()
                            if k not in ("_body", "_fm", "_searchable")})

    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    return results[:30]


def create_note(
    path: str,
    title: str,
    content: str,
    note_type: str = "note",
    topics: list = None,
    summary: str = "",
    date: str = None,
) -> dict:
    """볼트에 새 노트를 생성합니다."""
    from datetime import datetime
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    if topics is None:
        topics = []

    clean_path = path.lstrip("/").replace("..", "")
    if not clean_path.endswith(".md"):
        clean_path += ".md"

    fpath = VAULT_PATH / clean_path
    try:
        fpath.resolve().relative_to(VAULT_PATH.resolve())
    except ValueError:
        raise ValueError(f"Invalid path: {path}")

    fpath.parent.mkdir(parents=True, exist_ok=True)
    if fpath.exists():
        raise FileExistsError(f"Note already exists: {clean_path}")

    topics = _normalize_topics(topics)
    topics_str = "[" + ", ".join(f'"{t}"' for t in topics) + "]"
    frontmatter = (
        f"---\n"
        f"title: \"{title}\"\n"
        f"type: {note_type}\n"
        f"date: {date}\n"
        f"summary: \"{summary}\"\n"
        f"topics: {topics_str}\n"
        f"source: chatgpt\n"
        f"sensitivity: private\n"
        f"---\n\n"
    )
    fpath.write_text(frontmatter + content, encoding="utf-8")
    _invalidate_filelist()  # 방금 쓴 노트가 TTL 안에도 바로 검색되게
    return _note_from_file(fpath)


def write_raw_md(path: str, content: str, overwrite: bool = False) -> dict:
    """frontmatter 변형 없이 마크다운 파일을 경로 그대로 저장합니다.
    파일명·본문·frontmatter를 손대지 않고 원문 보존이 필요할 때 사용.
    차단 폴더(source/quarantine/accounts 등)는 거부."""
    clean_path = path.lstrip("/").replace("..", "")
    if not clean_path.endswith(".md"):
        clean_path += ".md"

    rel_dir = os.path.dirname(clean_path)
    if security.is_folder_blocked(rel_dir or clean_path):
        raise PermissionError(f"차단 폴더에는 쓸 수 없음: {rel_dir}")

    fpath = VAULT_PATH / clean_path
    try:
        fpath.resolve().relative_to(VAULT_PATH.resolve())
    except ValueError:
        raise ValueError(f"Invalid path: {path}")

    existed = fpath.exists()
    if existed and not overwrite:
        raise FileExistsError(f"Note already exists: {clean_path}")

    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(content, encoding="utf-8")
    _invalidate_filelist()  # 방금 쓴 노트가 TTL 안에도 바로 검색되게
    return {"path": clean_path, "bytes": len(content.encode("utf-8")),
            "overwritten": existed}


def update_note(
    path: str,
    append_content: str = None,
    title: str = None,
    summary: str = None,
    topics: list = None,
) -> dict:
    """기존 노트에 내용을 추가하거나 메타데이터를 수정합니다."""
    fpath = VAULT_PATH / path
    if not fpath.exists():
        matches = list(VAULT_PATH.rglob(f"*{Path(path).name}"))
        if not matches:
            raise FileNotFoundError(f"Note not found: {path}")
        fpath = matches[0]

    text = fpath.read_text(encoding="utf-8", errors="ignore")
    # 안전장치: relations: 멀티라인 블록이 있는 노트는 아래 naive 재직렬화가 블록을
    # 깨뜨린다(라인마다 ':' 파싱). 관계 수정은 set_person_relationship(라인 단위 패치)로.
    if re.search(r"(?m)^relations\s*:\s*$", text.split("\n---", 1)[0]):
        raise ValueError("relations 블록을 포함한 노트입니다. relationship 수정은 set_relationship 도구를 사용하세요.")
    fm, body = _parse_frontmatter(text)

    if title is not None:
        fm["title"] = title
    if summary is not None:
        fm["summary"] = summary
    if topics is not None:
        fm["topics"] = _normalize_topics(topics)

    if append_content:
        body = body.rstrip() + "\n\n" + append_content

    # Rebuild frontmatter
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            inner = ", ".join(f'"{x}"' for x in v)
            lines.append(f"{k}: [{inner}]")
        else:
            lines.append(f'{k}: "{v}"' if " " in str(v) or not v else f"{k}: {v}")
    lines.append("---\n")
    fpath.write_text("\n".join(lines) + body, encoding="utf-8")
    return _note_from_file(fpath)


def _fm_scalar(v) -> str:
    """frontmatter 스칼라를 naive 파서가 안전히 읽도록 직렬화.
    줄 기반 파서를 깨지 않게 개행/연속공백을 접고, 내부 큰따옴표는 작은따옴표로 바꾼다."""
    s = " ".join(str(v).split())
    return '"' + s.replace('"', "'") + '"'


def _dump_note(fm: dict, body: str) -> str:
    """frontmatter(dict) + 본문을 파일 텍스트로 직렬화. 본문은 그대로 보존."""
    lines = ["---"]
    for k, val in fm.items():
        if isinstance(val, list):
            lines.append(f"{k}: [" + ", ".join(_fm_scalar(x) for x in val) + "]")
        else:
            lines.append(f"{k}: {_fm_scalar(val)}")
    # 닫는 구분자에 개행을 붙이지 않는다 — 본문(body)이 닫는 '---' 뒤의 개행을 그대로
    # 보유하고 있어, "---\n"+body 로 쓰면 매 재기록마다 본문에 빈 줄이 하나씩 늘어난다.
    lines.append("---")
    return "\n".join(lines) + body


# enrich로 쓸 수 있는 메타데이터 화이트리스트 — 본문·민감도·출처는 절대 불가.
_ENRICH_FIELDS = ("summary", "topics", "entities", "category")


def enrich_note(path: str, summary: str = None, topics: list = None,
                entities: list = None, category: str = None) -> dict:
    """호출 LLM이 생성한 메타데이터(요약/토픽/엔티티/분류)를 노트 frontmatter에 반영한다.
    메타데이터 전용 — body·sensitivity·source 등은 건드리지 않는다. 민감/원문/격리 폴더와
    민감 노트는 거부. enriched=true·extraction=llm-callback 로 출처를 표시(사람 큐레이션과 구분)."""
    fpath = security.resolve_in_vault(VAULT_PATH, path)
    if fpath is None or not fpath.exists():
        raise FileNotFoundError(f"Note not found: {path}")
    rel = str(fpath.relative_to(VAULT_PATH))
    if security.is_folder_blocked(rel):
        return {"_blocked": True, "path": rel, "reason": "민감/원문/격리 폴더는 enrich 불가"}
    fm, body = _parse_frontmatter(fpath.read_text(encoding="utf-8", errors="ignore"))
    if security.is_sensitivity_blocked(fm.get("sensitivity"), honor_owner=False):
        return {"_blocked": True, "path": rel, "reason": "민감 노트는 enrich 불가"}
    patched = []
    if summary is not None:
        fm["summary"] = summary; patched.append("summary")
    if topics is not None:
        fm["topics"] = _normalize_topics(topics); patched.append("topics")
    if entities is not None:
        fm["entities"] = list(entities); patched.append("entities")
    if category is not None:
        fm["category"] = category; patched.append("category")
    fm["enriched"] = "true"
    fm["extraction"] = "llm-callback"
    fpath.write_text(_dump_note(fm, body), encoding="utf-8")  # body 그대로
    return {"path": rel, "enriched": True, "fields": patched}


def _patch_fm_field(text: str, key: str, value: str) -> str:
    """frontmatter 최상위 스칼라 key를 라인 단위로 교체/삽입. 다른 라인은 그대로 보존한다.
    naive 파서의 parse→rebuild 라운드트립이 relations: 같은 중첩 블록을 깨뜨리므로,
    관계 같은 민감한 노트에는 통째 재직렬화 대신 이 surgical 패치를 쓴다."""
    serial = f"{key}: " + (f'"{value}"' if re.search(r"[:#]|^\s|\s$", value) else value)
    if not text.startswith("---"):
        return f"---\n{serial}\n---\n\n" + text
    end = text.find("\n---", 3)
    if end == -1:
        return f"---\n{serial}\n---\n\n" + text
    head, fm, tail = text[:4], text[4:end], text[end:]  # "---\n" / fm본문 / "\n---...body"
    lines = fm.split("\n")
    pat = re.compile(rf"^{re.escape(key)}\s*:")
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines[i] = serial
            return head + "\n".join(lines) + tail
    # 없으면 name/title 다음(없으면 맨 앞)에 삽입
    insert_at = 0
    for i, ln in enumerate(lines):
        if re.match(r"^(name|title)\s*:", ln):
            insert_at = i + 1
            break
    lines.insert(insert_at, serial)
    return head + "\n".join(lines) + tail


def _resolve_person_note(person: str) -> Optional[Path]:
    """이름/별칭/entity_id/파일경로로 people 노트 파일을 찾는다."""
    pdir = VAULT_PATH / "people"
    if not pdir.exists():
        return None
    if person.endswith(".md") or "/" in person:
        rel = person if "/" in person else f"people/{person}"
        cand = security.resolve_in_vault(VAULT_PATH, rel)
        if cand and cand.exists():
            return cand
    pl = person.strip().lower()
    for fp in sorted(pdir.rglob("*.md")):
        if any(part in ("_templates", "_merged") for part in fp.parts):
            continue
        fm = _note_from_file(fp).get("_fm", {})
        names = {str(fm.get("name", "")).strip().lower(), fp.stem.lower(),
                 str(fm.get("entity_id", "")).strip().lower()}
        names |= {str(a).strip().lower() for a in (fm.get("aliases") or [])}
        if pl in {n for n in names if n}:
            return fp
    return None


def set_person_relationship(person: str, relationship: str) -> dict:
    """people 노트의 'relationship'(나와의 관계 라벨) 필드를 surgical 패치로 수정/설정한다.
    relations: 블록 등 다른 frontmatter는 그대로 보존된다. get_people 에 즉시 반영된다."""
    fpath = _resolve_person_note(person)
    if fpath is None:
        raise FileNotFoundError(f"인물 노트를 찾을 수 없음: {person}")
    text = fpath.read_text(encoding="utf-8", errors="ignore")
    fpath.write_text(_patch_fm_field(text, "relationship", relationship), encoding="utf-8")
    return {"path": str(fpath.relative_to(VAULT_PATH)), "relationship": relationship}


def list_unenriched(folder: Optional[str] = None, limit: int = 10) -> list[dict]:
    """enrich 안 된(요약/토픽 부재) 검색대상 노트를 본문(앞 4KB, PII 마스킹)과 함께 반환.
    호출 LLM이 summary/topics/entities 를 만들어 enrich_note 로 써넣는 용도. 민감 노트 제외."""
    out = []
    names = _third_party_names()
    for fpath in _iter_notes(folder):
        note = _cached_note(fpath)
        if not note:
            continue
        fm = note.get("_fm", {})
        if str(fm.get("enriched", "")).strip().lower() == "true":
            continue
        if note.get("summary") and fm.get("topics"):
            continue  # 이미 요약+토픽 보유 = 사실상 enriched
        if security.is_sensitivity_blocked(note.get("sensitivity"), honor_owner=False):
            continue
        body = security.redact_names(security.redact(note.get("_body", "")[:4000]), names)
        out.append({"path": note["path"], "title": note["title"], "body": body})
        if len(out) >= limit:
            break
    return out


def _resolve_entity_id(entity: str, nodes: dict) -> str:
    """평문 이름/별칭을 entity_id로 해석(대소문자·공백 무시). 이미 entity_id면 그대로.
    표기 규칙이 섞여 있어도(person:kim-jihyeon vs person:강유미) 이름·별칭으로 조회 가능.
    canonical ID 정규화 후의 구 ID(project:novalane 등)도 접미사를 별칭으로 재해석한다."""
    if entity in nodes:
        return entity
    target = str(entity).strip().casefold()
    # 구 entity_id 호환: "project:novalane" → 접미사 "novalane"을 이름/별칭으로 매칭
    suffix = target.split(":", 1)[1] if ":" in target else None
    for k, v in nodes.items():
        cands = {v.get("name", "").strip().casefold()}
        cands |= {str(a).strip().casefold() for a in (v.get("aliases") or [])}
        cands.discard("")
        if target in cands or (suffix and suffix in cands):
            return k
    return entity


def _edge_view(e: dict, side: str, names: dict) -> dict:
    """엣지 1건의 응답 뷰. P3: status/sources/생명주기 포함, 근거 없는 confirmed 금지."""
    status = str(e.get("status") or ("inferred" if e.get("inferred") else "proposed"))
    sources = e.get("sources") or []
    if status == "confirmed" and not sources:
        status = "proposed"  # 근거 없는 관계는 confirmed로 반환하지 않는다(위원회 P3)
    view = {"relation": e["relation"],
            side: e[side], f"{side}_name": names.get(e[side], ""),
            "status": status, "confidence": e.get("confidence", ""),
            "sources": sources,
            # 시간축(5차 P2): valid_from=실제 관계 시작일(모르면 unknown),
            # asserted_at=온톨로지에 기록된 날짜 — 둘을 섞지 않는다.
            "valid_from": e.get("valid_from") or "unknown",
            "valid_to": e.get("valid_to") or None,
            "asserted_at": e.get("asserted_at") or None}
    return view


def get_relations(entity: str) -> dict:
    """엔티티(entity_id 또는 이름/별칭)의 구조화 관계를 indexes/relations.json 에서 조회.
    P0(위원회 4차): owner 세션이 아니면 security.PUBLIC_RELATIONS 밖의 관계
    (가족·고용·그룹 소속 등)는 제외하고 redacted_categories 로만 표시한다 —
    민감 노트 전문 차단을 관계 그래프로 우회해 프로필을 재구성하는 경로 차단."""
    import json as _json_mod
    idx = VAULT_PATH / "indexes" / "relations.json"
    if not idx.exists():
        return {"error": "relations 인덱스 없음 (build_relations_index.py 실행 필요)"}
    data = _json_mod.loads(idx.read_text(encoding="utf-8"))
    nodes = data.get("nodes", {})
    names = {k: v.get("name", "") for k, v in nodes.items()}
    # 이름/별칭으로 들어오면 entity_id 로 해석(표기 혼재를 무해화 — item 5)
    entity = _resolve_entity_id(entity, nodes)
    is_owner = security.owner_active()
    out, inc, redacted = [], [], set()
    for e in data.get("edges", []):
        if entity not in (e["subject"], e["object"]):
            continue
        if not is_owner and e["relation"] not in security.PUBLIC_RELATIONS:
            redacted.add(security.relation_category(e["relation"]))
            continue
        if e["subject"] == entity:
            out.append(_edge_view(e, "object", names))
        if e["object"] == entity:
            inc.append(_edge_view(e, "subject", names))
    result = {"entity": entity, "name": names.get(entity, ""),
              "type": nodes.get(entity, {}).get("type", ""),
              "outgoing": out, "incoming": inc,
              "found": entity in nodes}
    if not is_owner:
        result["redacted_categories"] = sorted(redacted)
        if redacted:
            result["notice"] = ("일부 관계 범주는 owner 인증(Bearer 토큰) 세션에서만 "
                                "조회됩니다.")
    return result


def find_conversations_about(entity: str, limit: int = 20) -> dict:
    """엔티티(entity_id 또는 이름)를 언급한 대화 노트 목록 — RAG 근거 추적용."""
    import json as _json_mod
    idx = VAULT_PATH / "indexes" / "conversation_entities.json"
    if not idx.exists():
        return {"error": "conversation_entities 인덱스 없음 (build_conversation_links.py 실행 필요)"}
    data = _json_mod.loads(idx.read_text(encoding="utf-8"))
    by_entity = data.get("by_entity", {})
    convs = by_entity.get(entity)
    if convs is None:
        # 이름/별칭으로 들어오면 relations 인덱스의 nodes 로 entity_id 해석(표기 혼재 무해화 — item 5)
        ridx = VAULT_PATH / "indexes" / "relations.json"
        if ridx.exists():
            nodes = _json_mod.loads(ridx.read_text(encoding="utf-8")).get("nodes", {})
            resolved = _resolve_entity_id(entity, nodes)
            if resolved != entity:
                entity = resolved
                convs = by_entity.get(resolved)
    convs = convs or []
    return {"entity": entity, "count": len(convs), "conversations": convs[:limit]}


def get_topic_taxonomy() -> dict:
    """토픽을 상위 카테고리로 묶은 택소노미 (indexes/topic_taxonomy.json)."""
    import json as _json_mod
    idx = VAULT_PATH / "indexes" / "topic_taxonomy.json"
    if not idx.exists():
        return {"error": "topic_taxonomy 인덱스 없음 (build_topic_taxonomy.py 실행 필요)"}
    return _json_mod.loads(idx.read_text(encoding="utf-8"))


def get_all_topics(folder: str = None) -> dict:
    """볼트 전체 토픽 목록과 각 토픽의 노트 수."""
    counts: dict = {}
    for fpath in _iter_notes(folder):
        note = _cached_note(fpath)
        for t in note.get("topics", []):
            if t:
                counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def get_vault_stats() -> dict:
    """볼트 전체 통계. folder_counts=기본 검색 대상, excluded=존재하지만 기본 검색
    비대상인 폴더(명시 folder= 조회로 접근). 통계-실파일 불일치 오해를 막기 위한 표기."""
    counts = {}
    excluded = {}
    for base in _default_bases():
        n = ign = 0
        for p in base.rglob("*.md"):
            rel = str(p.relative_to(VAULT_PATH))
            if _is_okignored(rel):
                ign += 1
                continue
            if rel.startswith(_DEFAULT_SKIP_SUBPATHS):
                prefix = "/".join(rel.split("/")[:2])
                excluded[prefix] = excluded.get(prefix, 0) + 1
            else:
                n += 1
        if n:  # 빈 유틸 폴더(indexes/scripts/tools 등)는 통계에서 생략
            counts[base.name] = n
        if ign:
            excluded[f"{base.name} (.okignore)"] = ign
    for name in sorted(_DEFAULT_SKIP):
        d = VAULT_PATH / name
        if name.startswith((".", "_")) or not d.is_dir():
            continue
        c = sum(1 for _ in d.rglob("*.md"))
        if c:
            excluded[name] = c
    return {
        "vault_path": str(VAULT_PATH),
        "folder_counts": counts,
        "total": sum(counts.values()),
        "excluded": excluded,
        "ops": _ops_status(),
    }


def _ops_status() -> dict:
    """데이터 품질 관측 지표(위원회 4차 운영 지적) — 살아있는지가 아니라 정상인지.
    인덱스 신선도·볼트-인덱스 차이·후보 승인 대기·검색 지연을 한곳에 노출한다."""
    import json as _json_mod
    from datetime import datetime as _dt
    ops: dict = {}
    # 인덱스 신선도 + 규모
    for name in ("relations", "conversation_entities"):
        p = VAULT_PATH / "indexes" / f"{name}.json"
        if not p.exists():
            ops[name] = {"exists": False}
            continue
        info = {"exists": True,
                "updated": _dt.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
        if name == "relations":
            try:
                d = _json_mod.loads(p.read_text(encoding="utf-8"))
                info["nodes"] = d.get("node_count", 0)
                info["edges"] = d.get("edge_count", 0)
                # 볼트-인덱스 차이: entity_id 보유 노트 수 vs 인덱스 노드 수
                n_entities = 0
                for folder in ("people", "projects", "organizations",
                               "events", "decisions", "preferences"):
                    base = VAULT_PATH / folder
                    if not base.exists():
                        continue
                    for fp in base.rglob("*.md"):
                        if any(x in ("_templates", "_merged") for x in fp.parts):
                            continue
                        if _cached_note(fp).get("_fm", {}).get("entity_id"):
                            n_entities += 1
                info["vault_entities"] = n_entities
                info["index_gap"] = n_entities - info["nodes"]
            except Exception:
                pass
        ops[name] = info
    # 개인계층 후보 ledger: 상태별 수(승인 대기 = proposed)
    lp = VAULT_PATH / "indexes" / "personal_layer_ledger.json"
    if lp.exists():
        try:
            led = _json_mod.loads(lp.read_text(encoding="utf-8"))
            by_status: dict = {}
            for c in led.get("candidates", {}).values():
                s = c.get("status", "proposed")
                by_status[s] = by_status.get(s, 0) + 1
            ops["candidates"] = {"by_status": by_status,
                                 "pending": by_status.get("proposed", 0)}
            if led.get("last_run"):
                ops["candidates"]["last_run"] = _dt.fromtimestamp(
                    led["last_run"]).isoformat(timespec="seconds")
        except Exception:
            pass
    else:
        ops["candidates"] = {"by_status": {}, "pending": 0, "note": "ledger 없음(첫 --apply 전)"}
    # 검색 지연(프로세스 롤링 200회)
    if _SEARCH_TIMES:
        xs = sorted(_SEARCH_TIMES)
        ops["search_latency_ms"] = {
            "count": len(xs),
            "avg": round(sum(xs) / len(xs) * 1000, 1),
            "p95": round(xs[min(len(xs) - 1, int(len(xs) * 0.95))] * 1000, 1),
        }
    # 티어별 호출·차단 집계(최근 24h, audit.log — 5차 운영지표)
    try:
        import audit
        from datetime import datetime as _dt, timedelta as _td
        cutoff = (_dt.now() - _td(hours=24)).isoformat(timespec="seconds")
        agg = {"calls": 0, "owner": 0, "public": 0, "blocked": 0, "masked": 0, "errors": 0}
        if audit.LOG_FILE.exists():
            for line in audit.LOG_FILE.read_text(encoding="utf-8").splitlines()[-5000:]:
                try:
                    e = _json_mod.loads(line)
                except Exception:
                    continue
                if e.get("ts", "") < cutoff:
                    continue
                agg["calls"] += 1
                agg["owner" if e.get("owner") else "public"] += 1
                flags = (e.get("result") or {}).get("flags") or []
                if "blocked" in flags:
                    agg["blocked"] += 1
                if "masked" in flags:
                    agg["masked"] += 1
                if e.get("error"):
                    agg["errors"] += 1
        ops["audit_24h"] = agg
    except Exception:
        pass
    return ops


# 트리에서 절대 노출하지 않는 노이즈 디렉토리(점-디렉토리는 별도로 항상 제외).
_TREE_NOISE = {"opencrab_data", "Users", "_summaries", "schemas",
               "__pycache__", "node_modules"}


def get_vault_tree(subpath: Optional[str] = None, max_depth: int = 3) -> dict:
    """볼트 디렉토리 트리 + 폴더별 .md 수. content-blocked 폴더(source/kakao·accounts 등)는
    blocked=true 로 표시하고 내부 구조는 펼치지 않는다(이름/개수만 메타데이터로 노출)."""
    base = VAULT_PATH
    if subpath:
        cand = (VAULT_PATH / subpath).resolve()
        try:
            cand.relative_to(VAULT_PATH.resolve())
        except ValueError:
            return {"error": f"잘못된 경로: {subpath}"}
        base = cand
    if not base.exists() or not base.is_dir():
        return {"error": f"폴더 없음: {subpath or '.'}"}

    def walk(d: Path, depth: int) -> dict:
        rel = str(d.relative_to(VAULT_PATH)) if d != VAULT_PATH else "."
        blocked = rel != "." and security.is_folder_blocked(rel)
        node = {"name": d.name or VAULT_PATH.name, "path": rel,
                "md_files": sum(1 for _ in d.glob("*.md")), "blocked": blocked}
        # blocked 폴더는 존재/개수만 — 내부 구조는 펼치지 않는다(원격 노출 최소화).
        if not blocked and depth < max_depth:
            children = [walk(sub, depth + 1) for sub in sorted(d.iterdir())
                        if sub.is_dir() and sub.name not in _TREE_NOISE
                        and not sub.name.startswith(".")]
            if children:
                node["subfolders"] = children
        return node

    return walk(base, 0)
