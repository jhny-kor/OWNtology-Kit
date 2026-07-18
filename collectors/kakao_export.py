#!/usr/bin/env python3
"""Unattended KakaoTalk room backup via `katok` (full-history, DB-decrypting).

Unlike the Accessibility-based `kakao_self_backup.py` (kmsg), which can only read
the ~40-50 currently-rendered rows of one open chat, `katok` decrypts the local
SQLCipher database directly and keeps a normalized archive of *every* message in
*every* room. This collector:

    1. refreshes that archive   -> `katok sync --source macos --json`
    2. reads it (read-only)     -> ~/Library/Application Support/katok/archive.sqlite3
    3. emits kmsg-style JSON     -> source/kakao/kmsg-katok-<slug>.json
    4. (optionally) runs sync.py -> conversations/kakao/<chat>.md

The emitted JSON matches the shape `sync.py:sync_kakao()` already consumes
(`{"chat", "messages": [{author, body, time_raw_with_date}]}`), so the existing
hash-based dedup and Markdown rendering are reused unchanged. Timestamps are
converted UTC->KST and formatted exactly like kmsg ("YYYY-MM-DD 오후 H:MM") and
the account owner's nickname is mapped to "나", so messages that overlap an old
kmsg snapshot hash-collide and merge cleanly.

NOTE: katok does NOT capture "나와의 채팅" (the self-chat). That room stays with
`kakao_self_backup.py`. This script handles named/other rooms.

Usage:
    python3 collectors/kakao_export.py                  # 기본: 이름있는 방 + 1:1 + 그룹 전체
    python3 collectors/kakao_export.py --no-sync        # export only
    python3 collectors/kakao_export.py --all --min-messages 200
    python3 collectors/kakao_export.py --chat <방이름>
    python3 collectors/kakao_export.py --no-katok-sync  # use existing archive
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()
KAKAO_SOURCE = VAULT / "source" / "kakao"
KAKAO_OUT = VAULT / "conversations" / "kakao"
KAKAO_HASHES = KAKAO_SOURCE / ".hashes.json"
SYNC_SCRIPT = Path(__file__).resolve().parents[1] / "pipeline" / "sync_notes.py"
LOG_FILE = VAULT / ".sync.log"

from kitlib.config import load as _load_cfg
_CFG = _load_cfg()
_KAKAO_CFG = _CFG.get("kakao", {})

# 방 이름 변경 맵 {chat_id: "원하는 이름"} — config.json kakao.room_names,
# 웹 설정 화면에서 편집. 자동 생성된 방 이름을 영구 override 한다.
ROOM_NAMES_APPLIED = KAKAO_SOURCE / ".room_names_applied.json"  # collector-managed
ARCHIVE_DB = Path(
    "~/Library/Application Support/katok/archive.sqlite3"
).expanduser()

# Account owner's profile nickname; mapped to "나" for kmsg-compatible dedup.
# 비어 있으면 본인 메시지가 "나"로 매핑되지 않는다 — 웹 설정 화면에서 입력.
ME_NICKNAME = (_CFG.get("me", {}).get("kakao_nickname") or "").strip()

# 수집 제외 방(config kakao.exclude_rooms) — 이름 또는 chat_id로 지정. 민감 대화 제외용.
EXCLUDE_ROOMS = {str(x).strip().removeprefix("chat-")
                 for x in (_KAKAO_CFG.get("exclude_rooms") or []) if str(x).strip()}


def _excluded(chat_id: str, name: str) -> bool:
    if chat_id in EXCLUDE_ROOMS or name in EXCLUDE_ROOMS:
        log(f"제외: {name!r} (chat_id={chat_id}) — exclude_rooms")
        return True
    return False
KST = timezone(timedelta(hours=9))
UNKNOWN_NICK = "(알 수 없음)"

# nickname -> phone map (best-effort, from macOS Contacts) consumed by sync.py.
CONTACTS_MAP = KAKAO_SOURCE / ".contacts.json"
ADDRESSBOOK_GLOB = (
    "~/Library/Application Support/AddressBook/**/AddressBook-v22.abcddb"
)
# Kakao "플러스친구"/brand/official accounts to skip in --direct-by-participant.
SERVICE_RE = re.compile(
    r"카카오|삼성카드|현대카드|신한|롯데카드|국민카드|우리카드|하나카드|비씨카드|"
    r"캐치테이블|마이리얼트립|쿠팡|배달의민족|네이버|토스|페이$|카드$|은행|증권|"
    r"채널$|고객센터|알리미"
)


def _norm_phone(raw: str) -> str:
    """'+8210XXXXYYYY' / '//010...' -> '010-XXXX-YYYY' (best-effort)."""
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("82"):
        d = "0" + d[2:]
    if len(d) == 11 and d.startswith("010"):
        return f"{d[:3]}-{d[3:7]}-{d[7:]}"
    if len(d) == 10 and d.startswith("01"):
        return f"{d[:3]}-{d[3:6]}-{d[6:]}"
    return (raw or "").strip().lstrip("/")


def build_contacts_map() -> dict[str, str]:
    """name/nickname -> phone from every populated macOS AddressBook source."""
    import glob
    out: dict[str, str] = {}
    for db in glob.glob(os.path.expanduser(ADDRESSBOOK_GLOB), recursive=True):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            for last, first, nick, num in con.execute(
                "SELECT r.ZLASTNAME, r.ZFIRSTNAME, r.ZNICKNAME, p.ZFULLNUMBER "
                "FROM ZABCDRECORD r JOIN ZABCDPHONENUMBER p ON p.ZOWNER=r.Z_PK"
            ):
                ph = _norm_phone(num)
                if not ph:
                    continue
                full = ((last or "") + (first or "")).strip()
                for key in (full, (nick or "").strip()):
                    if key:
                        out.setdefault(unicodedata.normalize("NFC", key), ph)
            con.close()
        except Exception:
            continue
    return out


def _read_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def load_room_names() -> dict[str, str]:
    """{chat_id: desired name}, with the synthetic 'chat-' prefix stripped from keys."""
    out: dict[str, str] = {}
    for cid, name in (_KAKAO_CFG.get("room_names") or {}).items():
        key = str(cid).strip().removeprefix("chat-")
        if key and isinstance(name, str) and name.strip():
            out[key] = name.strip()
    return out


def _note_filename(name: str) -> str:
    # Must mirror sync.py:_kakao_note_path so we can find/remove orphan notes.
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "chat"


def cleanup_orphan_notes(orphan_names: set[str], keep: set[str]) -> None:
    """Remove auto-named notes left behind after a room was renamed via ROOM_NAMES."""
    targets = {n for n in orphan_names if n and n not in keep}
    if not targets:
        return
    hashes = _read_json(KAKAO_HASHES)
    removed = 0
    hashes_changed = False
    for old_name in targets:
        note = KAKAO_OUT / f"{_note_filename(old_name)}.md"
        if note.exists():
            note.unlink()
            removed += 1
        if old_name in hashes:
            del hashes[old_name]
            hashes_changed = True
    if hashes_changed:
        KAKAO_HASHES.write_text(
            json.dumps(hashes, ensure_ascii=False, indent=2), encoding="utf-8")
    if removed:
        log(f"cleaned up {removed} orphan note(s) after rename")


def log(msg: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] [kakao-katok] {msg}"
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def katok_sync() -> bool:
    """Refresh the archive from the live KakaoTalk DB. Non-fatal on failure."""
    if shutil.which("katok") is None:
        log("WARN: katok not on PATH (cargo install katok / brew install katok)")
        return False
    log("katok sync --source macos (refreshing archive from local DB)")
    proc = subprocess.run(
        ["katok", "sync", "--source", "macos", "--json"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        diag = (proc.stderr or proc.stdout or "").strip()[:300]
        log(f"WARN: katok sync failed (using existing archive): {diag}")
        return False
    try:
        info = json.loads((proc.stdout or "").strip() or "{}")
        total = info.get("total_messages") or info.get("last_sync", {}).get("total_messages")
        log(f"katok sync ok (total_messages={total})")
    except Exception:
        log("katok sync ok")
    return True


def _kst_kakao_time(iso: str) -> str:
    """'2022-04-02T10:09:24+00:00' -> '2022-04-02 오후 7:09' (KST, kmsg style)."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(KST)
    except Exception:
        return iso
    h24 = dt.hour
    ampm = "오전" if h24 < 12 else "오후"
    h12 = h24 % 12
    if h12 == 0:
        h12 = 12
    return f"{dt:%Y-%m-%d} {ampm} {h12}:{dt.minute:02d}"


# katok stores chats without a resolved title as the synthetic id "chat-<digits>".
_SYNTHETIC_NAME = re.compile(r"^chat-\d+$")


def list_chats(con: sqlite3.Connection, want: list[str] | None,
               all_chats: bool, named_only: bool, min_messages: int) -> list[tuple[str, str]]:
    """Return [(chat_id, chat_name)] to export."""
    rows = con.execute(
        "SELECT chat_id, chat_name, COUNT(*) c FROM messages "
        "WHERE text <> '' GROUP BY chat_id"
    ).fetchall()
    out: list[tuple[str, str]] = []
    if all_chats or named_only:
        for cid, name, c in rows:
            if c < min_messages:
                continue
            if named_only and _SYNTHETIC_NAME.match(name or ""):
                continue  # skip unnamed chat-<id> rooms (no readable note filename)
            out.append((cid, name))
    else:
        wanted = {unicodedata.normalize("NFC", w) for w in (want or [])}
        for cid, name, c in rows:
            if unicodedata.normalize("NFC", name or "") in wanted:
                out.append((cid, name))
        found = {unicodedata.normalize("NFC", n) for _, n in out}
        for missing in sorted(wanted - found):
            log(f"WARN: requested chat not found in katok archive: {missing!r}")
    return out


def list_direct_by_participant(con: sqlite3.Connection, min_messages: int,
                               include_services: bool) -> list[tuple[str, str]]:
    """Unnamed 1:1 rooms named after the other participant's nickname.

    Returns [(chat_id, nickname)]. Skips rooms whose only counterpart is
    "(알 수 없음)" and (unless include_services) Kakao brand/official accounts.
    """
    rows = con.execute(
        "SELECT chat_id, sender_nickname, COUNT(*) c FROM messages "
        "WHERE chat_name LIKE 'chat-%' AND chat_type='direct' AND text<>'' "
        "AND sender_nickname <> ? GROUP BY chat_id, sender_nickname",
        (ME_NICKNAME,),
    ).fetchall()
    best: dict[str, tuple[str, int]] = {}
    totals: dict[str, int] = {}
    for cid, nick, c in rows:
        totals[cid] = totals.get(cid, 0) + c
        if nick and nick != UNKNOWN_NICK and c > best.get(cid, ("", 0))[1]:
            best[cid] = (nick, c)
    out: list[tuple[str, str]] = []
    for cid, (nick, _) in best.items():
        if totals.get(cid, 0) < min_messages:
            continue
        if not include_services and SERVICE_RE.search(nick):
            continue
        out.append((cid, nick))
    return out


def list_groups_by_participants(con: sqlite3.Connection,
                                min_messages: int) -> list[tuple[str, str]]:
    """Unnamed group rooms (incl. open chats) named after top participants.

    katok stores no room title for these, so the note name is built from the
    most active members plus the member count, with the chat_id appended to keep
    it unique (different groups can share a top speaker). Returns [(chat_id,name)].
    """
    ids = [r[0] for r in con.execute(
        "SELECT chat_id FROM messages "
        "WHERE chat_name LIKE 'chat-%' AND chat_type='group' AND text<>'' "
        "GROUP BY chat_id HAVING COUNT(*) >= ?", (min_messages,)
    ).fetchall()]
    out: list[tuple[str, str]] = []
    for cid in ids:
        members = con.execute(
            "SELECT COUNT(DISTINCT sender_nickname) FROM messages "
            "WHERE chat_id=? AND sender_nickname<>?", (cid, ME_NICKNAME)
        ).fetchone()[0]
        tops = [r[0] for r in con.execute(
            "SELECT sender_nickname FROM messages "
            "WHERE chat_id=? AND text<>'' AND sender_nickname NOT IN (?, ?) "
            "GROUP BY sender_nickname ORDER BY COUNT(*) DESC LIMIT 2",
            (cid, ME_NICKNAME, UNKNOWN_NICK)
        ).fetchall()]
        if not tops:
            continue
        rest = max(members - len(tops), 0)
        label = ", ".join(tops) + (f" 외 {rest}명" if rest else "")
        out.append((cid, f"{label} ({cid})"))
    return out


def export_chat(con: sqlite3.Connection, chat_id: str, chat_name: str) -> dict:
    cur = con.execute(
        "SELECT sender_nickname, timestamp, text FROM messages "
        "WHERE chat_id = ? AND text <> '' ORDER BY timestamp, message_id",
        (chat_id,),
    )
    messages = []
    for sender, ts, text in cur:
        author = "나" if sender == ME_NICKNAME else (sender or "")
        body = (text or "").strip()
        if not body:
            continue
        messages.append({
            "author": author,
            "body": body,
            "time_raw_with_date": _kst_kakao_time(ts),
        })
    return {"chat": chat_name, "count": len(messages), "messages": messages}


def _snap_slug(chat_name: str) -> str:
    return re.sub(r"[^\w가-힣]", "", unicodedata.normalize("NFC", chat_name)) or "chat"


def write_snapshot(data: dict, chat_name: str, slug: str | None = None) -> Path:
    KAKAO_SOURCE.mkdir(parents=True, exist_ok=True)
    if slug is None:
        slug = _snap_slug(chat_name)
    # Stable filename per chat (overwritten each run); katok holds full history,
    # so a single current snapshot per room is authoritative.
    path = KAKAO_SOURCE / f"kmsg-katok-{slug}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_sync() -> int:
    log("running sync.py")
    proc = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT)],
        cwd=str(VAULT), capture_output=True, text=True,
    )
    if proc.stdout:
        for ln in proc.stdout.splitlines():
            if "Kakao" in ln or "카카오" in ln:
                log(ln.strip())
    if proc.returncode != 0:
        log(f"WARN: sync.py exited {proc.returncode}: {(proc.stderr or '').strip()[:300]}")
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="Unattended KakaoTalk room backup via katok")
    ap.add_argument("--chat", action="append", dest="chats", default=None,
                    help="chat name to export; repeat for several. Default: whitelist")
    ap.add_argument("--chat-id", action="append", dest="chat_ids", default=None,
                    metavar="ID[=NAME]",
                    help="export a specific chat_id (e.g. unnamed chat-<id> rooms); "
                         "use ID=NAME to file it under NAME so it merges into that "
                         "room's note. Repeat for several.")
    ap.add_argument("--all", action="store_true",
                    help="export every chat with >= --min-messages messages")
    ap.add_argument("--named-only", action="store_true",
                    help="like --all but skip unnamed 'chat-<id>' rooms (readable filenames)")
    ap.add_argument("--min-messages", type=int,
                    default=int(_KAKAO_CFG.get("min_messages", 1)),
                    help="skip chats below this many text messages (default: config)")
    ap.add_argument("--direct-by-participant", action="store_true",
                    help="name unnamed 1:1 'chat-<id>' rooms after the other person's "
                         "nickname; enriches notes with a phone from macOS Contacts.")
    ap.add_argument("--groups-by-participants", action="store_true",
                    help="collect unnamed group/open-chat 'chat-<id>' rooms, named "
                         "after top participants + member count (chat_id kept unique).")
    ap.add_argument("--include-services", action="store_true",
                    default=bool(_KAKAO_CFG.get("include_services")),
                    help="with --direct-by-participant, keep Kakao brand/official accounts")
    ap.add_argument("--no-katok-sync", action="store_true",
                    help="skip 'katok sync'; read the existing archive as-is")
    ap.add_argument("--no-sync", action="store_true", help="export only, skip sync.py")
    args = ap.parse_args()

    # 선택 플래그가 없으면 기본 프리셋: 이름있는 방 전체 + 1:1(상대 닉네임 명명)
    # + 그룹/오픈채팅(참여자 기반 명명) — 화이트리스트 없이 전체 수집.
    if not (args.chats or args.all or args.named_only or args.chat_ids
            or args.direct_by_participant or args.groups_by_participants):
        args.named_only = True
        args.direct_by_participant = True
        args.groups_by_participants = True

    if not ME_NICKNAME:
        log("WARN: config me.kakao_nickname 미설정 — 본인 메시지가 '나'로 매핑되지 않음 "
            "(웹 설정 화면 또는 config.json에서 입력)")

    # 나와의 채팅(self-chat): katok이 방 이름을 못 잡으므로 chat_id를 config로 지정
    self_chat = str(_KAKAO_CFG.get("self_chat_id") or "").strip().removeprefix("chat-")
    if self_chat:
        args.chat_ids = list(args.chat_ids or [])
        already = {s.partition("=")[0].strip().removeprefix("chat-") for s in args.chat_ids}
        if self_chat not in already:
            args.chat_ids.append(f"{self_chat}={ME_NICKNAME or '나와의 채팅'}")

    if not args.no_katok_sync:
        katok_sync()

    if not ARCHIVE_DB.exists():
        log(f"ERROR: katok archive not found: {ARCHIVE_DB}")
        return 2

    con = sqlite3.connect(f"file:{ARCHIVE_DB}?mode=ro&immutable=1", uri=True)
    exported = 0
    explicit = bool(args.chats or args.all or args.named_only or args.chat_ids
                    or args.direct_by_participant or args.groups_by_participants)
    run_whitelist = bool(args.chats or args.all or args.named_only) or not explicit
    group_min = args.min_messages if (args.all or args.named_only) else 0

    room_names = load_room_names()
    prev_applied = _read_json(ROOM_NAMES_APPLIED)
    applied: dict[str, str] = {}
    orphan_names: set[str] = set()

    def resolve(chat_id: str, default: str) -> str:
        """Apply the user rename map and track auto/previous names now orphaned."""
        final = room_names.get(chat_id, default)
        applied[chat_id] = final
        if final != default:
            orphan_names.add(default)
        prev = prev_applied.get(chat_id)
        if prev and prev != final:
            orphan_names.add(prev)
        return final

    try:
        # Named/whitelist selection (default when no selection flag is given).
        if run_whitelist:
            for chat_id, chat_name in list_chats(
                con, args.chats, args.all, args.named_only, args.min_messages
            ):
                name = resolve(chat_id, chat_name)
                if _excluded(chat_id, name):
                    continue
                data = export_chat(con, chat_id, name)
                if name != chat_name:
                    # Renamed: write under a stable id slug and drop the old
                    # name-based snapshot so sync.py won't re-create the old note.
                    path = write_snapshot(data, name, slug=f"id-{chat_id}")
                    old = KAKAO_SOURCE / f"kmsg-katok-{_snap_slug(chat_name)}.json"
                    if old.exists() and old != path:
                        old.unlink()
                else:
                    path = write_snapshot(data, name)
                log(f"exported {data['count']} msgs from {name!r} -> {path.name}")
                exported += 1

        # Unnamed 1:1 rooms named by the other participant, with phone enrichment.
        if args.direct_by_participant:
            contacts = build_contacts_map()
            matched: dict[str, str] = {}
            for chat_id, nick in list_direct_by_participant(
                con, args.min_messages if (args.all or args.named_only) else 0,
                args.include_services,
            ):
                name = resolve(chat_id, nick)
                if _excluded(chat_id, name):
                    continue
                data = export_chat(con, chat_id, name)
                if data["count"] == 0:
                    continue
                data["chat_type"] = "direct"  # 상대별 인물 스텁 생성용 마커
                write_snapshot(data, name, slug=f"id-{chat_id}")
                exported += 1
                phone = contacts.get(unicodedata.normalize("NFC", name))
                if phone:
                    matched[name] = phone
            # Merge phone map for sync.py (don't drop earlier entries).
            existing = _read_json(CONTACTS_MAP)
            existing.update(matched)
            CONTACTS_MAP.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"direct-by-participant: {len(matched)} phones matched")

        # Unnamed group / open-chat rooms, named after top participants.
        if args.groups_by_participants:
            n = 0
            for chat_id, auto in list_groups_by_participants(con, group_min):
                name = resolve(chat_id, auto)
                if _excluded(chat_id, name):
                    continue
                data = export_chat(con, chat_id, name)
                if data["count"] == 0:
                    continue
                write_snapshot(data, name, slug=f"id-{chat_id}")
                exported += 1
                n += 1
            log(f"groups-by-participants: {n} group/open-chat rooms")

        # Explicit chat_id selection (e.g. unnamed chat-<id> rooms). "ID=NAME"
        # files the export under NAME so it merges into that room's note.
        for spec in (args.chat_ids or []):
            raw_id, _, label = spec.partition("=")
            chat_id = raw_id.strip().removeprefix("chat-")
            name = resolve(chat_id, label.strip() or f"chat-{chat_id}")
            if _excluded(chat_id, name):
                continue
            data = export_chat(con, chat_id, name)
            if data["count"] == 0:
                log(f"WARN: chat_id {chat_id!r} has no text messages (skipped)")
                continue
            path = write_snapshot(data, name, slug=f"id-{chat_id}")
            log(f"exported {data['count']} msgs from chat_id {chat_id} as {name!r} -> {path.name}")
            exported += 1

        # Persist applied rename state + clean up notes left under old names.
        cleanup_orphan_notes(orphan_names, keep=set(applied.values()))
        ROOM_NAMES_APPLIED.write_text(
            json.dumps(applied, ensure_ascii=False, indent=2), encoding="utf-8")

        if exported == 0:
            log("no matching chats to export")
            return 3
    finally:
        con.close()

    if not args.no_sync:
        run_sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
