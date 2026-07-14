#!/usr/bin/env python3
"""SMS/iMessage exporter for owntology.

Reads chats via the `imsg` CLI (needs Full Disk Access for chat.db) and writes
one JSON snapshot per chat into source/sms/ in the exact format sync.py's
sync_sms() consumes:

    sms-<SERVICE>-<identifier>-<chat_id>.json
    {"meta": {chat_id, identifier, service, exported_at}, "messages": [...]}

Existing snapshots are merged (dedupe by date+sender+text), so reruns are safe
and history is never truncated by the per-run --limit window.

Usage:
    python3 source/sms_export.py            # all recent chats, limit 500 each
    python3 source/sms_export.py --limit 2000
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1])); from kitlib.config import vault_path as _vp; VAULT = _vp()
SMS_SOURCE = VAULT / "source" / "sms"

def safe_name(identifier: str) -> str:
    return re.sub(r'[/\\:*?"<>|\s]', "_", identifier)


def list_chats() -> list[tuple[int, str, str]]:
    """Return [(chat_id, identifier, service)] from `imsg chats --json` (JSONL)."""
    out = subprocess.check_output(
        ["imsg", "chats", "--json"], text=True, stderr=subprocess.DEVNULL)
    chats = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        ident = (c.get("identifier") or c.get("name") or "").strip()
        if not ident or c.get("id") is None:
            continue
        chats.append((int(c["id"]), ident, (c.get("service") or "SMS").strip()))
    return chats


def fetch_history(chat_id: int, limit: int) -> list[dict]:
    out = subprocess.check_output(
        ["imsg", "history", "--chat-id", str(chat_id), "--limit", str(limit), "--json"],
        text=True, stderr=subprocess.DEVNULL,
    )
    rows = []
    stripped = out.strip()
    if stripped.startswith("["):
        try:
            rows = json.loads(stripped)
        except json.JSONDecodeError:
            rows = []
    else:
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    msgs = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("is_reaction"):
            continue
        text = (r.get("text") or "").strip()
        if not text:
            continue
        msgs.append({
            "date": (r.get("created_at") or r.get("date") or "").strip(),
            "sender": (r.get("sender") or "").strip(),
            "is_from_me": bool(r.get("is_from_me")),
            "text": text,
        })
    return msgs


def _msg_key(m: dict) -> str:
    return f"{m.get('date','')}|{m.get('sender','')}|{m.get('text','')}"


def export_chat(chat_id: int, identifier: str, service: str, limit: int) -> tuple[Path | None, int]:
    msgs = fetch_history(chat_id, limit)
    if not msgs:
        return None, 0

    SMS_SOURCE.mkdir(parents=True, exist_ok=True)

    # Reuse the existing snapshot for this chat regardless of name details
    existing_path = next(iter(sorted(SMS_SOURCE.glob(f"sms-*-{chat_id}.json"))), None)
    old_msgs: list[dict] = []
    old_meta: dict = {}
    if existing_path is not None:
        try:
            old = json.loads(existing_path.read_text(encoding="utf-8"))
            old_msgs = old.get("messages", [])
            old_meta = old.get("meta", {}) or {}
        except Exception:
            pass

    merged: dict[str, dict] = {_msg_key(m): m for m in old_msgs}
    new_count = 0
    for m in msgs:
        k = _msg_key(m)
        if k not in merged:
            merged[k] = m
            new_count += 1

    # Legacy snapshots have date-less rows; once a dated copy of the same
    # (sender, text) exists, drop the date-less duplicate.
    dated = {(m.get("sender", ""), m.get("text", ""))
             for m in merged.values() if m.get("date")}
    dropped = [k for k, m in merged.items()
               if not m.get("date") and (m.get("sender", ""), m.get("text", "")) in dated]
    for k in dropped:
        del merged[k]

    service = old_meta.get("service") or service
    path = SMS_SOURCE / f"sms-{service}-{safe_name(identifier)}-{chat_id}.json"
    if new_count == 0 and not dropped and existing_path == path:
        return path, 0

    all_msgs = sorted(merged.values(), key=lambda m: m.get("date", ""))
    meta = {
        "chat_id": chat_id,
        "identifier": identifier,
        "service": service,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(
        json.dumps({"meta": meta, "messages": all_msgs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if existing_path is not None and existing_path != path:
        existing_path.unlink()  # masked-name snapshot replaced by raw-name file
    return path, new_count


def main() -> int:
    ap = argparse.ArgumentParser(description="Export SMS/iMessage chats to source/sms/")
    ap.add_argument("--limit", type=int, default=500, help="messages per chat per run")
    args = ap.parse_args()

    if shutil.which("imsg") is None:
        print("[sms-export] ERROR: imsg not found on PATH", file=sys.stderr)
        return 2

    try:
        chats = list_chats()
    except subprocess.CalledProcessError:
        print(
            "[sms-export] ERROR: imsg cannot read ~/Library/Messages/chat.db. "
            "Grant Full Disk Access to the app running this script "
            "(System Settings > Privacy & Security > Full Disk Access), "
            "or run ./one_touch_sync.sh from a terminal that already has it.",
            file=sys.stderr,
        )
        return 3

    total_new = 0
    for chat_id, identifier, service in chats:
        try:
            path, n = export_chat(chat_id, identifier, service, args.limit)
        except subprocess.CalledProcessError as e:
            print(f"[sms-export] chat {chat_id} failed: {e}", file=sys.stderr)
            continue
        if path and n:
            print(f"[sms-export] {path.name}: +{n}")
            total_new += n

    print(f"[sms-export] done: {len(chats)} chats, {total_new} new messages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
