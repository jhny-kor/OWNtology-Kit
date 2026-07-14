"""
owntology kakao reader — 카카오톡 원문 메시지 적재 / 검색 / 인물·업무 추출

데이터 소스: {VAULT}/source/kakao/kmsg-*.json
  각 파일 = 한 채팅방 export
    { "chat": "방이름", "count": N, "messages": [ {author, body, time_raw_with_date}, ... ] }

성능: 전체 ~30만 메시지(77MB). 매 호출 풀파싱은 병목이므로
      디렉토리 시그니처(파일수+최대 mtime) 기반 모듈 캐시를 사용한다.
      소스가 바뀌지 않으면 첫 호출 1회만 파싱하고 이후는 메모리에서 처리한다.
"""

import os
import re
import json
import glob
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import vault
import security

KAKAO_DIR = vault.VAULT_PATH / "source" / "kakao"
PEOPLE_DIR = vault.VAULT_PATH / "people"

# ── 모듈 캐시 ────────────────────────────────────────────────
_LOCK = threading.Lock()
_CACHE: dict = {"sig": None, "rooms": None, "failures": None, "built_at": None}
_ALIAS_CACHE: dict = {"sig": None, "map": None, "people": None}


def _dir_signature(directory: Path, pattern: str) -> tuple:
    """디렉토리 내 파일 집합의 시그니처 (개수 + 최대 mtime)."""
    files = glob.glob(str(directory / pattern))
    if not files:
        return (0, 0.0)
    mx = 0.0
    for f in files:
        try:
            m = os.path.getmtime(f)
            if m > mx:
                mx = m
        except OSError:
            continue
    return (len(files), mx)


# ── 한국어 시각 파싱 ─────────────────────────────────────────
_TIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:\s+(오전|오후)\s*(\d{1,2}):(\d{2}))?")


def parse_kakao_time(raw: str) -> Optional[datetime]:
    """'2023-06-28 오후 12:29' / '2026-06-03' → datetime. 실패 시 None."""
    if not raw:
        return None
    m = _TIME_RE.match(raw.strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    ampm, hh, mm = m.group(4), m.group(5), m.group(6)
    if ampm is None:
        try:
            return datetime(y, mo, d)
        except ValueError:
            return None
    hour = int(hh)
    minute = int(mm)
    if ampm == "오전":
        if hour == 12:
            hour = 0
    else:  # 오후
        if hour != 12:
            hour += 12
    try:
        return datetime(y, mo, d, hour, minute)
    except ValueError:
        return None


def _iso(dt: Optional[datetime]) -> str:
    return dt.isoformat() if dt else ""


def _room_id_from_path(path: str) -> str:
    """파일명에서 방 식별자 추출."""
    stem = Path(path).stem  # kmsg-katok-id-253602693014015
    m = re.match(r"kmsg-katok-id-(\d+)", stem)
    if m:
        return m.group(1)
    m = re.match(r"kmsg-chat-(.+?)-\d{6,}", stem)
    if m:
        return "chat-" + m.group(1)
    return stem


# ── 로드 / 그룹화 ────────────────────────────────────────────
def _load_rooms(force: bool = False) -> tuple:
    """
    (rooms, failures, built_at) 반환. 방이름 기준 그룹화 + (author,time,body) 중복 제거.

    room 구조:
      { room_id, name, source_files:[...], count, last_dt, members:set,
        messages:[ {id, author, body, time_raw, dt} ] }
    """
    sig = _dir_signature(KAKAO_DIR, "kmsg-*.json")
    with _LOCK:
        if not force and _CACHE["sig"] == sig and _CACHE["rooms"] is not None:
            return _CACHE["rooms"], _CACHE["failures"], _CACHE["built_at"]

        groups: dict = {}   # name -> room dict
        failures = []
        files = sorted(glob.glob(str(KAKAO_DIR / "kmsg-*.json")))

        for fpath in files:
            try:
                with open(fpath, encoding="utf-8") as fh:
                    raw = fh.read()
                if not raw.strip():
                    continue  # 빈 재export 파일은 실패가 아니라 무시
                data = json.loads(raw)
            except Exception as e:  # noqa: BLE001
                failures.append({"file": Path(fpath).name, "error": str(e)})
                continue
            if not isinstance(data, dict):
                failures.append({"file": Path(fpath).name, "error": "not an object"})
                continue
            name = (data.get("chat") or Path(fpath).stem).strip()
            msgs = data.get("messages") or []
            rid = _room_id_from_path(fpath)

            room = groups.get(name)
            if room is None:
                room = {
                    "room_id": rid,
                    "name": name,
                    "source_files": [],
                    "_seen": set(),
                    "messages": [],
                }
                groups[name] = room
            room["source_files"].append(Path(fpath).name)
            # 가장 메시지가 많은 파일의 id를 대표 room_id로
            if len(msgs) and rid.isdigit():
                room.setdefault("_maxcount", 0)
                if len(msgs) > room["_maxcount"]:
                    room["_maxcount"] = len(msgs)
                    room["room_id"] = rid

            seen = room["_seen"]
            for mm in msgs:
                if not isinstance(mm, dict):
                    continue
                author = (mm.get("author") or "").strip()
                body = mm.get("body") or ""
                traw = mm.get("time_raw_with_date") or mm.get("time") or ""
                key = (author, traw, body)
                if key in seen:
                    continue
                seen.add(key)
                room["messages"].append({
                    "author": author,
                    "body": body,
                    "time_raw": traw,
                    "dt": parse_kakao_time(traw),
                })

        # 마무리: 정렬, 메타 계산
        rooms = []
        for room in groups.values():
            room.pop("_seen", None)
            room.pop("_maxcount", None)
            msgs = room["messages"]
            # dt 있는 것 우선 시간순 정렬 (없으면 끝으로)
            msgs.sort(key=lambda x: (x["dt"] is None, x["dt"] or datetime.min))
            for i, mm in enumerate(msgs):
                mm["id"] = f"{room['room_id']}:{i}"
            members = {}
            last_dt = None
            for mm in msgs:
                a = mm["author"]
                if a:
                    members[a] = members.get(a, 0) + 1
                if mm["dt"] and (last_dt is None or mm["dt"] > last_dt):
                    last_dt = mm["dt"]
            room["members"] = members
            room["count"] = len(msgs)
            room["last_dt"] = last_dt
            rooms.append(room)

        rooms.sort(key=lambda r: (r["last_dt"] is None, r["last_dt"] or datetime.min), reverse=True)
        built_at = datetime.now()
        _CACHE.update({"sig": sig, "rooms": rooms, "failures": failures, "built_at": built_at})
        return rooms, failures, built_at


# ── 인물 별칭 맵 ─────────────────────────────────────────────
def _parse_people_aliases(text: str) -> tuple:
    """people 노트에서 (canonical_title, [aliases]) 추출. inline/multiline YAML 모두 지원."""
    title = ""
    aliases = []
    in_aliases = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("title:") and not title:
            title = s.split(":", 1)[1].strip().strip('"').strip("'")
        if s.startswith("aliases:"):
            rest = s.split(":", 1)[1].strip()
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1]
                aliases += [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
                in_aliases = False
            elif rest in ("", "[]"):
                in_aliases = rest == ""
            else:
                aliases.append(rest.strip('"').strip("'"))
                in_aliases = False
            continue
        if in_aliases:
            if s.startswith("- "):
                aliases.append(s[2:].strip().strip('"').strip("'"))
            elif s and not s.startswith("#"):
                in_aliases = False
    return title, [a for a in aliases if a]


def _alias_map() -> tuple:
    """({alias_lower: canonical}, [people]) 캐시."""
    sig = _dir_signature(PEOPLE_DIR, "*.md")
    if _ALIAS_CACHE["sig"] == sig and _ALIAS_CACHE["map"] is not None:
        return _ALIAS_CACHE["map"], _ALIAS_CACHE["people"]
    amap = {}
    people = []
    for fpath in glob.glob(str(PEOPLE_DIR / "*.md")):
        try:
            text = Path(fpath).read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        title, aliases = _parse_people_aliases(text)
        if not title:
            title = Path(fpath).stem
        is_canonical = "canonical: true" in text.lower()
        people.append({"canonical": title, "aliases": aliases, "path": f"people/{Path(fpath).name}"})
        amap[title.lower()] = title
        for a in aliases:
            # canonical:true 노트가 별칭 소유권을 우선 차지
            if is_canonical or a.lower() not in amap:
                amap[a.lower()] = title
    _ALIAS_CACHE.update({"sig": sig, "map": amap, "people": people})
    return amap, people


# ── 방/사람 매칭 ─────────────────────────────────────────────
def match_rooms(chatroom: Optional[str]) -> list:
    """방이름으로 매칭 (정확 우선, 없으면 부분일치). None이면 전체."""
    rooms, _, _ = _load_rooms()
    if not chatroom:
        return rooms
    q = chatroom.strip().lower()
    exact = [r for r in rooms if r["name"].lower() == q]
    if exact:
        return exact
    return [r for r in rooms if q in r["name"].lower()]


def _sender_match(author: str, sender: Optional[str], alias_map: dict) -> bool:
    if not sender:
        return True
    a = author.lower()
    s = sender.strip().lower()
    if s in a or a in s:
        return True
    # 별칭 → canonical 비교
    canon_s = alias_map.get(s)
    canon_a = alias_map.get(a)
    if canon_s and canon_a and canon_s == canon_a:
        return True
    if canon_s and canon_s.lower() in a:
        return True
    return False


def _evidence(room: dict, m: dict) -> dict:
    """공통 근거 필드."""
    return {
        "chatroom_id": room["room_id"],
        "chatroom_name": room["name"],
        "sender_name": m["author"],
        "message_id": m["id"],
        "sent_at": _iso(m["dt"]),
        "evidence_text": m["body"],
    }


# ── 공개 API: 9개 도구 ───────────────────────────────────────
def sync_status() -> dict:
    rooms, failures, built_at = _load_rooms()
    total = sum(r["count"] for r in rooms)
    last = None
    for r in rooms:
        if r["last_dt"] and (last is None or r["last_dt"] > last):
            last = r["last_dt"]
    return {
        "status": "complete" if not failures else "partial",
        "last_message_at": _iso(last),
        "indexed_at": _iso(built_at),
        "chatroom_count": len(rooms),
        "message_count": total,
        "parse_failure_count": len(failures),
        "parse_failures": failures[:20],
        "source_dir": str(KAKAO_DIR),
    }


def list_chatrooms(limit: int = 100) -> list:
    rooms, _, _ = _load_rooms()
    out = []
    for r in rooms[:limit]:
        out.append({
            "chatroom_id": r["room_id"],
            "name": r["name"],
            "type": "group" if len(r["members"]) > 2 else "direct",
            "member_count": len(r["members"]),
            "message_count": r["count"],
            "last_message_at": _iso(r["last_dt"]),
        })
    return out


def list_members(chatroom: str) -> dict:
    rooms = match_rooms(chatroom)
    if not rooms:
        return {"error": f"채팅방을 찾을 수 없음: {chatroom}", "members": []}
    alias_map, _ = _alias_map()
    # 사람별 집계 (매칭된 방 합산)
    agg: dict = {}
    for room in rooms:
        for m in room["messages"]:
            a = m["author"]
            if not a:
                continue
            e = agg.setdefault(a, {"display_name": a, "message_count": 0, "last_active_at": None})
            e["message_count"] += 1
            if m["dt"] and (e["last_active_at"] is None or m["dt"] > e["last_active_at"]):
                e["last_active_at"] = m["dt"]
    members = []
    for a, e in agg.items():
        canon = alias_map.get(a.lower())
        members.append({
            "display_name": a,
            "canonical_name": canon or a,
            "aliases": [],
            "message_count": e["message_count"],
            "last_active_at": _iso(e["last_active_at"]),
        })
    members.sort(key=lambda x: -x["message_count"])
    return {
        "chatroom_name": ", ".join(r["name"] for r in rooms),
        "member_count": len(members),
        "members": members,
    }


def get_messages(chatroom: str, sender: Optional[str] = None,
                 days: int = 7, limit: int = 100) -> dict:
    rooms = match_rooms(chatroom)
    if not rooms:
        return {"error": f"채팅방을 찾을 수 없음: {chatroom}", "messages": []}
    alias_map, _ = _alias_map()
    cutoff = datetime.now() - timedelta(days=days) if days else None
    collected = []
    for room in rooms:
        for m in room["messages"]:
            if cutoff and (m["dt"] is None or m["dt"] < cutoff):
                continue
            if not _sender_match(m["author"], sender, alias_map):
                continue
            collected.append((room, m))
    # 최신순 후 limit, 그다음 다시 시간순 출력
    collected.sort(key=lambda rm: (rm[1]["dt"] is None, rm[1]["dt"] or datetime.min), reverse=True)
    collected = collected[:limit]
    collected.sort(key=lambda rm: (rm[1]["dt"] is None, rm[1]["dt"] or datetime.min))
    msgs = []
    for room, m in collected:
        msgs.append({
            "message_id": m["id"],
            "chatroom_name": room["name"],
            "sender_name": m["author"],
            "sent_at": _iso(m["dt"]),
            "text": security.redact(m["body"]),
        })
    return {
        "chatroom_name": ", ".join(r["name"] for r in rooms),
        "sender": sender,
        "days": days,
        "returned": len(msgs),
        "limit": limit,
        "messages": msgs,
    }


def search_messages(chatroom: Optional[str] = None, sender: Optional[str] = None,
                    keyword: Optional[str] = None, date_from: Optional[str] = None,
                    date_to: Optional[str] = None, limit: int = 100) -> dict:
    rooms = match_rooms(chatroom)
    if not rooms:
        return {"error": f"채팅방을 찾을 수 없음: {chatroom}", "messages": []}
    alias_map, _ = _alias_map()
    kw = keyword.lower() if keyword else None

    def _parse_d(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d") if s else None
        except ValueError:
            return None
    dfrom = _parse_d(date_from)
    dto = _parse_d(date_to)
    if dto:
        dto = dto + timedelta(days=1)  # inclusive

    hits = []
    for room in rooms:
        for m in room["messages"]:
            if dfrom and (m["dt"] is None or m["dt"] < dfrom):
                continue
            if dto and (m["dt"] is None or m["dt"] >= dto):
                continue
            if not _sender_match(m["author"], sender, alias_map):
                continue
            if kw and kw not in (m["body"] or "").lower():
                continue
            hits.append((room, m))
    hits.sort(key=lambda rm: (rm[1]["dt"] is None, rm[1]["dt"] or datetime.min), reverse=True)
    total = len(hits)
    hits = hits[:limit]
    msgs = []
    for room, m in hits:
        msgs.append({
            "message_id": m["id"],
            "chatroom_name": room["name"],
            "sender_name": m["author"],
            "sent_at": _iso(m["dt"]),
            "text": security.redact(m["body"]),
        })
    return {
        "query": {"chatroom": chatroom, "sender": sender, "keyword": keyword,
                  "date_from": date_from, "date_to": date_to},
        "total_matched": total,
        "returned": len(msgs),
        "limit": limit,
        "messages": msgs,
    }


def resolve_person_alias(name: str, chatroom: Optional[str] = None) -> dict:
    """호칭/별칭 → canonical 인물. people 노트 + 방 멤버 매칭."""
    alias_map, people = _alias_map()
    n = name.strip()
    nl = n.lower()

    # 1) people 노트 정확/별칭 매칭
    if nl in alias_map:
        canon = alias_map[nl]
        conf = 1.0 if alias_map.get(nl) == canon and canon.lower() == nl else 0.95
        return {"input_name": name, "canonical_name": canon,
                "resolved": True, "confidence": round(conf, 2),
                "method": "people_alias", "chatroom": chatroom}

    # 2) 방 멤버 author 부분일치
    rooms = match_rooms(chatroom)
    candidates = {}
    for room in rooms:
        for a, cnt in room["members"].items():
            if nl in a.lower() or a.lower() in nl:
                candidates[a] = candidates.get(a, 0) + cnt
    if candidates:
        best = max(candidates.items(), key=lambda x: x[1])
        canon = alias_map.get(best[0].lower(), best[0])
        # 후보 다수면 신뢰도 하향
        conf = 0.8 if len(candidates) == 1 else 0.55
        return {"input_name": name, "canonical_name": canon,
                "matched_author": best[0], "resolved": True,
                "confidence": conf, "method": "room_member",
                "candidates": sorted(candidates, key=candidates.get, reverse=True)[:5],
                "chatroom": chatroom}

    return {"input_name": name, "canonical_name": None, "resolved": False,
            "confidence": 0.0, "method": "none", "chatroom": chatroom}


# 업무 추출용 한국어 키워드 휴리스틱
_KW = {
    "completed": ["완료", "끝냈", "끝났", "처리했", "배포했", "반영했", "마무리", "했습니다", "했어요", "적용완료"],
    "in_progress": ["진행중", "진행 중", "하는중", "하고있", "작업중", "보고있", "확인중", "테스트중"],
    "requested": ["부탁", "요청", "해주세", "해주실", "가능할까", "해줄 수", "해주면", "필요합니", "검토 부탁"],
    "reported_issues": ["장애", "오류", "에러", "버그", "안됨", "안돼", "실패", "문제", "이슈", "터졌", "다운"],
    "upcoming": ["예정", "할 예정", "내일", "다음주", "다음 주", "이번주", "이번 주", "마감", "까지", "예정일", "스케줄", "일정"],
}
_DATE_HINT = re.compile(r"(\d{1,2}\s*월\s*\d{1,2}\s*일|\d{4}-\d{2}-\d{2}|내일|모레|다음\s*주|이번\s*주|월요일|화요일|수요일|목요일|금요일|마감|까지)")


def _bucket(body: str) -> list:
    b = body or ""
    tags = []
    for cat, kws in _KW.items():
        if any(k in b for k in kws):
            tags.append(cat)
    return tags


def _iter_filtered(chatroom: Optional[str] = None, sender: Optional[str] = None,
                   days: Optional[int] = None):
    """캐시된 방을 직접 순회하며 (room, msg) 를 yield. 중간 리스트/정렬 없음.

    find_projects / upcoming_tasks 처럼 전수 스캔이 필요한 분석 도구가
    search_messages(limit=100000) 으로 29만 메시지를 dict 화/정렬하던 낭비를 제거한다.
    """
    rooms = match_rooms(chatroom)
    alias_map, _ = _alias_map()
    cutoff = (datetime.now() - timedelta(days=days)) if days else None
    for room in rooms:
        for m in room["messages"]:
            if cutoff and (m["dt"] is None or m["dt"] < cutoff):
                continue
            if not _sender_match(m["author"], sender, alias_map):
                continue
            yield room, m


def summarize_person(chatroom: str, sender: str, days: int = 30) -> dict:
    """특정 인물의 최근 활동을 카테고리별 근거 메시지와 함께 반환 (휴리스틱)."""
    rooms = match_rooms(chatroom)
    if not rooms:
        return {"error": f"채팅방을 찾을 수 없음: {chatroom}", "messages": []}
    # 원문으로 먼저 분류하고, 실제 반환되는 근거(버킷당 20건)만 redact 한다.
    # (이전엔 get_messages(limit=10000)가 최대 1만 개 본문 전부에 마스킹 정규식을 돌렸음)
    matched = []
    total = 0
    for room, m in _iter_filtered(chatroom, sender, days):
        total += 1
        tags = _bucket(m["body"] or "")
        if tags:
            matched.append((m, tags))
    matched.sort(key=lambda x: (x[0]["dt"] is None, x[0]["dt"] or datetime.min))
    buckets = {k: [] for k in _KW}
    evidence_ids = []
    for m, tags in matched:
        for t in tags:
            if len(buckets[t]) < 20:
                buckets[t].append({
                    "message_id": m["id"],
                    "sent_at": _iso(m["dt"]),
                    "evidence_text": security.redact(m["body"] or "")[:200],
                })
            if m["id"] not in evidence_ids:
                evidence_ids.append(m["id"])
    return {
        "chatroom_name": ", ".join(r["name"] for r in rooms),
        "sender_name": sender,
        "days": days,
        "message_count": total,
        "note": "키워드 휴리스틱 분류 결과이며 근거 메시지 원문을 함께 제공합니다. 최종 요약은 호출 측 LLM이 근거를 검토해 작성하세요.",
        "completed": buckets["completed"],
        "in_progress": buckets["in_progress"],
        "requested": buckets["requested"],
        "reported_issues": buckets["reported_issues"],
        "upcoming": buckets["upcoming"],
        "evidence_message_ids": evidence_ids[:60],
    }


def find_projects(chatroom: Optional[str] = None, sender: Optional[str] = None,
                  days: Optional[int] = None) -> dict:
    """대화에서 프로젝트/시스템명 언급 추출 + 기존 Project 엔티티 연결."""
    if chatroom and not match_rooms(chatroom):
        return {"error": f"채팅방을 찾을 수 없음: {chatroom}", "projects": []}
    # 기존 프로젝트 엔티티
    try:
        known = vault.get_projects()
    except Exception:  # noqa: BLE001
        known = []
    pmap = {p["title"]: p for p in known if p.get("title")}
    if not pmap:
        return {
            "query": {"chatroom": chatroom, "sender": sender, "days": days},
            "matched_known_projects": 0,
            "note": "projects/ 폴더에 등록된 프로젝트 엔티티가 없어 매칭 대상이 없습니다.",
            "projects": [],
        }
    # 프로젝트명을 단일 정규식으로 합쳐 메시지당 1회 C-레벨 스캔 (N×P → N)
    pat = re.compile("|".join(re.escape(t) for t in sorted(pmap, key=len, reverse=True)))

    hits: dict = {}
    for room, m in _iter_filtered(chatroom, sender, days):
        body = m["body"] or ""
        found = {mo.group() for mo in pat.finditer(body)}
        for title in found:
            p = pmap[title]
            e = hits.setdefault(title, {
                "project_name": title,
                "status": p.get("status", ""),
                "related_people": set(),
                "evidence_message_ids": [],
                "last_mentioned_at": "",
            })
            e["related_people"].add(m["author"])
            if len(e["evidence_message_ids"]) < 10:
                e["evidence_message_ids"].append(m["id"])
            sent = _iso(m["dt"])
            if sent > e["last_mentioned_at"]:
                e["last_mentioned_at"] = sent
    projects = []
    for e in hits.values():
        e["related_people"] = sorted(e["related_people"])
        projects.append(e)
    projects.sort(key=lambda x: x["last_mentioned_at"], reverse=True)
    return {
        "query": {"chatroom": chatroom, "sender": sender, "days": days},
        "matched_known_projects": len(projects),
        "note": "기존 projects/ 엔티티명과 대화 원문을 매칭한 결과입니다.",
        "projects": projects,
    }


def upcoming_tasks(chatroom: Optional[str] = None, assignee: Optional[str] = None,
                   days: int = 30) -> dict:
    """대화에서 예정 업무/일정 추출 (휴리스틱 + 근거)."""
    if chatroom and not match_rooms(chatroom):
        return {"error": f"채팅방을 찾을 수 없음: {chatroom}", "tasks": []}
    tasks = []
    for room, m in _iter_filtered(chatroom, assignee, days):
        body = m["body"] or ""
        tags = _bucket(body)
        date_match = _DATE_HINT.search(body)
        has_date = bool(date_match)
        # 미래/예정 신호(예정 키워드 또는 날짜 단서)가 있는 메시지만
        if not ("upcoming" in tags or has_date):
            continue
        confidence = 0.5
        if "upcoming" in tags:
            confidence += 0.2
        if has_date:
            confidence += 0.2
        tasks.append({
            "title": body[:80],
            "assignee": m["author"],
            "chatroom_name": room["name"],
            "due_hint": (date_match.group(0) if has_date else ""),
            "status": "planned",
            "confidence": round(min(confidence, 0.95), 2),
            "sent_at": _iso(m["dt"]),
            "evidence_message_ids": [m["id"]],
        })
    tasks.sort(key=lambda x: (x["confidence"], x["sent_at"]), reverse=True)
    return {
        "query": {"chatroom": chatroom, "assignee": assignee, "days": days},
        "note": "키워드/날짜 휴리스틱 추출입니다. confidence와 근거 메시지를 확인하세요.",
        "task_count": len(tasks),
        "tasks": tasks[:100],
    }
