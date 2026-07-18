#!/usr/bin/env python3
"""
owntology-kit sync_notes — source/의 카카오톡·SMS 원문을 conversations/ 대화 노트로 변환
수동 실행: python3 pipeline/sync_notes.py
"""

import csv, hashlib, json, re, sys, unicodedata

from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()
KAKAO_OUT        = VAULT / "conversations/kakao"
KAKAO_SOURCE_DIR = VAULT / "source/kakao"
# 같은 방이 이름 변경(이모지↔텍스트)으로 갈라진 경우 하나로 병합 — config kakao.chat_aliases
from kitlib.config import load as _load_cfg
KAKAO_CHAT_ALIASES = _load_cfg().get("kakao", {}).get("chat_aliases") or {}
KAKAO_HASHES     = KAKAO_SOURCE_DIR / ".hashes.json"
KAKAO_PROCESSED  = KAKAO_SOURCE_DIR / ".processed.json"
KAKAO_CONTACTS   = KAKAO_SOURCE_DIR / ".contacts.json"  # chat_name -> phone (best-effort)
SMS_SOURCE_DIR   = VAULT / "source/sms"
SMS_OUT          = VAULT / "conversations/sms"
SMS_STATE_FILE   = VAULT / ".sync_sms_state.json"


# ── KakaoTalk CSV + kmsg JSON ────────────────────────────

def _nfc(s: str) -> str:
    return unicodedata.normalize('NFC', s)

def _kakao_canon(name: str) -> str:
    return KAKAO_CHAT_ALIASES.get(name, name)

def _kakao_aliases(name: str) -> list[str]:
    return sorted({str(old).strip() for old, new in KAKAO_CHAT_ALIASES.items()
                   if str(new).strip() == name and str(old).strip() != name})

def _kakao_chat_name(filename: str) -> str:
    stem = Path(filename).stem
    prefix = "KakaoTalk_Chat_"
    if stem.startswith(prefix):
        rest = re.sub(r'_\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$', '', stem[len(prefix):])
        return _kakao_canon(_nfc(rest))
    return _kakao_canon(_nfc(stem))

def _kakao_msg_hash(row: dict) -> str:
    return hashlib.md5(f"{row['Date']}|{row['User']}|{row['Message']}".encode()).hexdigest()

def _parse_kakao_csv(filepath: Path) -> list[dict]:
    rows = []
    with open(filepath, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            rows.append({
                'Date': row.get('Date', '').strip(),
                'User': row.get('User', '').strip(),
                'Message': row.get('Message', '').strip(),
            })
    return rows

# 카카오 파싱 실패 격리·집계 (위원회 P1-5). 이전엔 silent drop이었다.
_KAKAO_PARSE_FAILURES: list[dict] = []


def _record_kakao_parse_failure(filepath: Path, reason: str):
    """파싱 실패 파일을 quarantine/parse-failures/ 에 복사하고 사유를 집계한다."""
    rel = str(filepath)
    _KAKAO_PARSE_FAILURES.append({"file": filepath.name, "reason": reason})
    try:
        qdir = VAULT / "quarantine" / "parse-failures"
        qdir.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(filepath, qdir / filepath.name)
    except Exception:
        pass


def _parse_kmsg_json(filepath: Path) -> tuple[str, list[dict]]:
    try:
        text = filepath.read_text(encoding='utf-8').strip()
        if not text.startswith('{'):
            _record_kakao_parse_failure(filepath, "not-json (no leading '{')")
            return '', []
        data = json.loads(text)
    except Exception as e:
        _record_kakao_parse_failure(filepath, f"json-decode: {type(e).__name__}")
        return '', []
    chat_name = _nfc(data.get('chat', '').strip() or filepath.stem)
    rows = []
    for msg in data.get('messages', []):
        author = (msg.get('author') or '').strip()
        if author == '(me)':
            author = '나'
        body = (msg.get('body') or '').strip()
        if not body:
            continue
        rows.append({'Date': (msg.get('time_raw_with_date') or '').strip(),
                     'User': author, 'Message': body})
    return chat_name, rows

def _kakao_note_path(chat_name: str) -> Path:
    # Kakao nicknames can contain path-illegal chars (e.g. "이안/youjin").
    safe = re.sub(r'[\\/:*?"<>|]', '_', chat_name).strip() or 'chat'
    return KAKAO_OUT / f"{safe}.md"

def _load_kakao_contacts() -> dict:
    if KAKAO_CONTACTS.exists():
        try:
            return json.loads(KAKAO_CONTACTS.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}

def _kakao_room_tag(chat_name: str) -> str:
    """Sanitized room tag, or '' for auto participant-based group names."""
    if re.search(r'\(\d{4,}\)\s*$', chat_name) or ', ' in chat_name:
        return ''  # auto group/open-chat name ("… 외 N명 (id)") — too noisy as a tag
    return re.sub(r'[^\w가-힣]', '', unicodedata.normalize('NFC', chat_name))


def _save_kakao_md(chat_name: str, all_rows: list[dict], all_names: set[str] | None = None,
                   aliases: list[str] | None = None):
    by_date: dict[str, list] = {}
    for row in all_rows:
        date_part = row['Date'].split(' ')[0] if ' ' in row['Date'] else row['Date']
        by_date.setdefault(date_part, []).append(row)

    phone = _load_kakao_contacts().get(chat_name)
    tags = ['kakao', 'conversation']
    room_tag = _kakao_room_tag(chat_name)
    if room_tag and room_tag not in tags:
        tags.append(room_tag)
    lines = ['---', 'type: conversation', 'source: kakao',
             f'chat: {chat_name}', f'updated: {datetime.now().strftime("%Y-%m-%d")}',
             f'messages: {len(all_rows)}', f'tags: [{", ".join(tags)}]']
    if aliases:
        lines.append(f'aliases: {json.dumps(aliases, ensure_ascii=False)}')
    if phone:
        lines.append(f'phone: "{phone}"')
    # 증거사슬 (P1-5): 카카오 원문이 근거, 자동 추출
    _dates = [r['Date'][:10] for r in all_rows if r.get('Date')]
    lines += [
        f'source_ids: ["kakao:{chat_name}"]',
        f'valid_from: {min(_dates) if _dates else ""}',
        f'valid_to: {max(_dates) if _dates else ""}',
        'confidence: 0.8', 'extraction: auto',
        'sensitivity: private', '---', '',
        f'# 카카오톡 — {chat_name}', '']

    # 관련 노트: 이 방 참여자 중 본인의 다른 카톡 노트가 있는 사람에게 [[링크]]
    if all_names:
        related = sorted({
            r['User'] for r in all_rows
            if r['User'] and r['User'] not in ('나', chat_name) and r['User'] in all_names
        })
        if related:
            lines += ['## 관련 노트', '',
                      ' '.join(f'[[{n}]]' for n in related), '']

    for date in sorted(by_date.keys()):
        lines += [f'## {date}', '']
        for row in by_date[date]:
            parts = row['Date'].split(' ', 1)
            time_part = parts[1] if len(parts) > 1 else ''
            lines += [f"**{row['User']}** `{time_part}`",
                      row['Message'].replace('\n', '  \n'), '']
    _kakao_note_path(chat_name).write_text('\n'.join(lines), encoding='utf-8')

def sync_kakao() -> int:
    if not KAKAO_SOURCE_DIR.exists():
        return 0
    KAKAO_OUT.mkdir(parents=True, exist_ok=True)

    all_hashes = json.loads(KAKAO_HASHES.read_text(encoding='utf-8')) if KAKAO_HASHES.exists() else {}
    processed  = json.loads(KAKAO_PROCESSED.read_text(encoding='utf-8')) if KAKAO_PROCESSED.exists() else {}

    by_chat: dict[str, list[tuple[Path, str]]] = {}
    for f in sorted(KAKAO_SOURCE_DIR.glob("KakaoTalk_Chat_*.csv")):
        by_chat.setdefault(_kakao_chat_name(f.name), []).append((f, 'csv'))
    for f in sorted(KAKAO_SOURCE_DIR.glob("kmsg-*.json")):
        try:
            text = f.read_text(encoding='utf-8').strip()
            if not text.startswith('{'):
                continue
            name = _kakao_canon(_nfc(json.loads(text).get('chat', '').strip() or f.stem))
        except Exception as e:
            _record_kakao_parse_failure(f, f"name-resolve: {type(e).__name__}")
            continue
        by_chat.setdefault(name, []).append((f, 'json'))

    total_new = 0
    all_names = set(by_chat.keys())  # every kakao note name (for [[관련 노트]] linking)
    for chat_name, file_list in by_chat.items():
        existing = set(all_hashes.get(chat_name, []))
        rows_map: dict[str, dict] = {}
        new_count = 0
        for f, ftype in file_list:
            rows = _parse_kakao_csv(f) if ftype == 'csv' else _parse_kmsg_json(f)[1]
            for row in rows:
                h = _kakao_msg_hash(row)
                if h not in rows_map:
                    rows_map[h] = row
                    if h not in existing:
                        new_count += 1
            processed[f.name] = True

        if new_count == 0 and _kakao_note_path(chat_name).exists():
            continue

        all_rows = sorted(rows_map.values(), key=lambda r: r['Date'])
        _save_kakao_md(chat_name, all_rows, all_names, _kakao_aliases(chat_name))
        all_hashes[chat_name] = list(rows_map.keys())
        total_new += new_count
        print(f"  [Kakao] {chat_name} — {len(all_rows)}개 (신규 {new_count}개)")

    KAKAO_HASHES.write_text(json.dumps(all_hashes, ensure_ascii=False, indent=2), encoding='utf-8')
    KAKAO_PROCESSED.write_text(json.dumps(processed, ensure_ascii=False, indent=2), encoding='utf-8')

    # 파싱 실패 집계·리포트 (P1-5) — 더 이상 silent drop 하지 않는다.
    if _KAKAO_PARSE_FAILURES:
        report = VAULT / "policies" / "kakao-parse-failures.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({
            "count": len(_KAKAO_PARSE_FAILURES),
            "failures": _KAKAO_PARSE_FAILURES,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"  [Kakao] ⚠️ 파싱 실패 {len(_KAKAO_PARSE_FAILURES)}건 — "
              f"quarantine/parse-failures/ 격리, 리포트: {report.name}")
    return total_new


# ── SMS JSON -> Markdown ──────────────────────────────────

def _sms_local(iso: str) -> str:
    """imsg dates are UTC ISO ('...Z') — render in local time (KST)."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return (iso or "")[:16].replace("T", " ")

def sync_sms() -> int:
    if not SMS_SOURCE_DIR.exists():
        return 0
    SMS_OUT.mkdir(parents=True, exist_ok=True)
    state = json.loads(SMS_STATE_FILE.read_text()) if SMS_STATE_FILE.exists() else {"processed": {}}
    processed = state.get("processed", {})

    new_count = 0
    for json_path in sorted(SMS_SOURCE_DIR.glob("sms-*.json")):
        stat = json_path.stat()
        file_key = f"{json_path.name}:{stat.st_mtime:.0f}"
        if processed.get(json_path.name) == file_key:
            continue
        try:
            data = json.loads(json_path.read_text(encoding='utf-8'))
        except Exception as e:
            print(f"  [SMS] skip {json_path.name}: {e}")
            continue
        meta = data.get("meta", {}) or {}
        messages = data.get("messages", [])
        if not messages:
            continue
        identifier = meta.get("identifier", json_path.stem)
        service = meta.get("service", "SMS")
        chat_id = meta.get("chat_id", "")
        exported_at = meta.get("exported_at", "")
        first = messages[0]
        last = messages[-1]
        first_date = _sms_local(first.get("date") or "")[:10]
        last_date = _sms_local(last.get("date") or "")[:10]
        by_date = {}
        for msg in messages:
            date_part = _sms_local(msg.get("date") or "")[:10] or "날짜미상"
            by_date.setdefault(date_part, []).append(msg)
        lines = [
            "---",
            "type: conversation",
            "source: sms",
            f"service: {service}",
            f"identifier: {identifier}",
            f"chat_id: {chat_id}",
            f"exported_at: {exported_at}",
            f"updated: {datetime.now().strftime('%Y-%m-%d')}",
            f"messages: {len(messages)}",
            "tags: [sms, conversation]",
            "sensitivity: private",
            "---",
            "",
            f"# SMS — {identifier}",
            "",
            f"- 기간: {first_date} ~ {last_date}",
            f"- 발신자: {identifier}",
            ""
        ]
        for date in sorted(by_date.keys()):
            lines += [f"## {date}", ""]
            for msg in by_date[date]:
                sender = msg.get("sender", "unknown")
                is_mine = msg.get("is_from_me", False)
                label = "나" if is_mine else sender
                time = _sms_local(msg.get("date") or "")[11:16]
                text = (msg.get("text") or "").strip().replace("\n", "  ")
                lines += [f"**{label}** `{time}`", text, ""]
        out_path = SMS_OUT / f"{service}-{identifier}-{chat_id}.md"
        out_path.write_text("\n".join(lines), encoding='utf-8')
        processed[json_path.name] = file_key
        new_count += 1
        print(f"  [SMS] {out_path.name}")
    state["processed"] = processed
    SMS_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    return new_count


# ── 메인 ─────────────────────────────────────────────────
def main():
    print(f"owntology-kit sync_notes — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    kakao_new = sync_kakao()
    sms_new   = sync_sms()
    print(f"\n완료: Kakao +{kakao_new}개, SMS +{sms_new}개 신규 저장")

if __name__ == "__main__":
    main()
