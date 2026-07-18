#!/usr/bin/env python3
"""Apple Notes (Notes.app) → conversations/notes/*.md exporter for owntology.

Reads every note from Notes.app via JXA (osascript -l JavaScript; the first run
prompts once for Automation permission) and writes one Markdown note per item
into conversations/notes/. Idempotent: each note maps to a stable filename
(title + short id hash), so re-runs overwrite in place. A per-note modification
timestamp is tracked in .export_state.json so unchanged notes are skipped.

Usage:
    python3 source/notes_export.py            # all notes (incremental)
    python3 source/notes_export.py --full     # rewrite every note
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()
NOTES_OUT = VAULT / "conversations" / "notes"
STATE_FILE = NOTES_OUT / ".export_state.json"
RAW_OUT = VAULT / "source" / "apple-notes" / "raw" / "latest.json"
LOG_FILE = VAULT / ".sync.log"
SENSITIVE_RE = re.compile(r"(비번|비밀번호|password|passcode|otp|인증번호|pin|락커)", re.I)

JXA = r"""
function run() {
  const Notes = Application("Notes");
  const out = [];
  const all = Notes.notes;
  const n = all.length;
  for (let i = 0; i < n; i++) {
    try {
      const note = all[i];
      let folder = "";
      try { folder = note.container().name(); } catch (e) {}
      out.push({
        id: note.id(),
        name: note.name(),
        body: note.plaintext(),
        folder: folder,
        created: note.creationDate().toISOString(),
        modified: note.modificationDate().toISOString(),
      });
    } catch (e) {}
  }
  return JSON.stringify(out);
}
"""


def log(msg: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] [notes] {msg}"
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def fetch_notes() -> list[dict]:
    proc = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", JXA],
        capture_output=True, text=True,
    )
    out = (proc.stdout or "").strip()
    if not out.startswith("["):
        diag = (proc.stderr or out or "").strip()[:300]
        log(f"ERROR: Notes JXA returned no JSON (permission?): {diag}")
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        log(f"ERROR: could not parse Notes JSON: {e}")
        return []


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name).strip()[:80] or "note"


def _filename(note: dict) -> str:
    h = hashlib.md5(note["id"].encode()).hexdigest()[:8]
    title = _safe(note.get("name") or "untitled")
    return f"{title}-{h}.md"


def archive_raw_notes(notes: list[dict]) -> None:
    RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(notes, ensure_ascii=False, indent=2, sort_keys=True)
    if RAW_OUT.exists() and RAW_OUT.read_text(encoding="utf-8", errors="ignore") == raw:
        return
    RAW_OUT.write_text(raw + "\n", encoding="utf-8")


def write_note(note: dict) -> None:
    body = (note.get("body") or "").strip()
    title = (note.get("name") or "untitled").strip()
    filename = _filename(note)
    source_id = filename[:-3]
    # Notes' plaintext repeats the title as the first body line; drop the dupe.
    lines = body.split("\n")
    if lines and lines[0].strip() == title:
        body = "\n".join(lines[1:]).strip()
    sensitivity = "sensitive" if SENSITIVE_RE.search(f"{title}\n{body}") else "private"
    folder = (note.get("folder") or "").strip()
    tags = ["apple-notes", "note"]
    folder_tag = re.sub(r"[^\w가-힣]", "", folder)
    if folder_tag and folder_tag not in tags:
        tags.append(folder_tag)
    fm = ["---", "type: note", "source: apple-notes",
          f"title: {json.dumps(title, ensure_ascii=False)}",
          f"folder: {json.dumps(folder, ensure_ascii=False)}",
          f"created: {json.dumps(note.get('created', ''), ensure_ascii=False)}",
          f"updated: {json.dumps(note.get('modified', ''), ensure_ascii=False)}",
          f'tags: [{", ".join(tags)}]',
          f"sensitivity: {sensitivity}",
          f'source_path: "conversations/notes/{filename}"',
          f'source_raw: "{RAW_OUT.relative_to(VAULT).as_posix()}"',
          f'source_ids: ["apple-notes:{source_id}"]',
          f"valid_from: {note.get('modified', '')}",
          "valid_to: ",
          "confidence: 0.7",
          "extraction: auto",
          "---", "", f"# {title}", "", body, ""]
    (NOTES_OUT / filename).write_text("\n".join(fm), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Apple Notes → conversations/notes exporter")
    ap.add_argument("--full", action="store_true", help="rewrite every note, ignore state")
    args = ap.parse_args()

    NOTES_OUT.mkdir(parents=True, exist_ok=True)
    state = {}
    if STATE_FILE.exists() and not args.full:
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    notes = fetch_notes()
    if not notes:
        return 2
    archive_raw_notes(notes)

    written = 0
    for note in notes:
        nid = note.get("id")
        if not nid or not (note.get("name") or note.get("body")):
            continue
        if not args.full and state.get(nid) == note.get("modified"):
            continue
        write_note(note)
        state[nid] = note.get("modified")
        written += 1

    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"exported {written} note(s) (total {len(notes)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
