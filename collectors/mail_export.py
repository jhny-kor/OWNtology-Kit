#!/usr/bin/env python3
"""Mail.app → source/email/*.md exporter for owntology.

Pulls recent messages from Mail.app's unified inbox via JXA (osascript -l
JavaScript; first run prompts once for Automation permission) and writes one
Markdown note per message into source/email/, matching the frontmatter shape
already used there. one_touch_sync.py keeps source/email/ as the preserved raw
source layer; conversations/email/ is reserved for future summary nodes.

Dedupe: message ids are tracked in source/email/.export_state.json.

Usage:
    python3 source/mail_export.py              # latest 100 inbox messages
    python3 source/mail_export.py --limit 300
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

import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1])); from kitlib.config import vault_path as _vp; VAULT = _vp()
EMAIL_SOURCE = VAULT / "source" / "email"
STATE_FILE = EMAIL_SOURCE / ".export_state.json"

JXA = r"""
function run(argv) {
  const limit = parseInt(argv[0] || "300", 10);
  const days = parseInt(argv[1] || "14", 10);
  const Mail = Application("Mail");

  // Pull fresh mail first — Mail.app's local store is stale unless it has
  // been running; reading without this returns whatever was last fetched.
  try { Mail.checkForNewMail(); delay(12); } catch (e) {}

  const cutoff = new Date(Date.now() - days * 86400 * 1000);
  let targets = [];
  try {
    targets = Mail.inbox.messages.whose({ dateReceived: { _greaterThan: cutoff } })();
  } catch (e) {
    // Fallback: scan the head of the unified inbox and filter in JS
    const msgs = Mail.inbox.messages;
    const n = Math.min(limit * 2, msgs.length);
    for (let i = 0; i < n; i++) {
      try {
        if (msgs[i].dateReceived() > cutoff) targets.push(msgs[i]);
      } catch (e2) {}
    }
  }

  const out = [];
  for (let i = 0; i < targets.length && out.length < limit; i++) {
    try {
      const m = targets[i];
      out.push({
        id: String(m.messageId()),
        date: m.dateReceived().toISOString(),
        sender: String(m.sender()),
        subject: String(m.subject() || ""),
        content: String(m.content() || "").slice(0, 20000),
        account: String(m.mailbox().account().name()),
      });
    } catch (e) {}
  }
  return JSON.stringify(out);
}
"""

_STOPWORDS = {
    "있습니다", "합니다", "안내", "메일", "이메일", "the", "and", "for", "your",
    "you", "from", "with", "있는", "대한", "관련",
}


def fetch_messages(limit: int, days: int) -> list[dict]:
    proc = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", JXA, str(limit), str(days)],
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "osascript failed").strip()[:300])
    out = (proc.stdout or "").strip()
    if not out.startswith("["):
        raise RuntimeError(f"unexpected osascript output: {out[:200]}")
    return json.loads(out)


def _keywords(subject: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9]{2,}|[가-힣]{2,}", subject)
    out, seen = [], set()
    for t in tokens:
        low = t.lower()
        if low in _STOPWORDS or low in seen:
            continue
        seen.add(low)
        out.append(t)
        if len(out) >= 6:
            break
    return out


def _summary(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if len(line) >= 8 and not line.startswith(("http", "<", "[image")):
            return line[:100].replace('"', "'")
    return ""


def _sender_domain(sender: str) -> str:
    m = re.search(r"@([\w.\-]+)", sender)
    return m.group(1).lower() if m else ""


def _yaml_str(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', "'") + '"'


def _local_date(iso_utc: str) -> str:
    """Filename dates follow local time (legacy notes did) — UTC dates shift
    anything received 00:00–09:00 KST to the previous day and create dupes."""
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return (iso_utc or "")[:10] or datetime.now().strftime("%Y-%m-%d")


def make_note(msg: dict) -> tuple[str, str]:
    date_str = _local_date(msg.get("date") or "")
    subject = (msg.get("subject") or "(제목 없음)").strip()
    content = (msg.get("content") or "").strip()
    keywords = _keywords(subject)
    summary = _summary(content)

    safe = re.sub(r'[/\\:*?"<>|\n]', "_", subject).strip()[:50]
    digest = hashlib.md5((msg.get("id") or subject).encode()).hexdigest()[:6]
    fname = f"{date_str}_{safe or 'no-subject'}_{digest}.md"

    lines = [
        "---",
        f"date: {_yaml_str(msg.get('date', ''))}",
        f"from: {_yaml_str(msg.get('sender', ''))}",
        f"subject: {_yaml_str(subject)}",
        f"account: {_yaml_str(msg.get('account', ''))}",
        "tags: [email, source]",
        "source_type: email",
        f"source_service: {_yaml_str(msg.get('account', ''))}",
        f"source_sender_domain: {_yaml_str(_sender_domain(msg.get('sender', '')))}",
        f"summary: {_yaml_str(summary)}",
        f"keywords: {json.dumps(keywords, ensure_ascii=False)}",
        "entities: []",
        "topics: [\"이메일\"]",
        "people: []",
        'project: ""',
        "sensitivity: private",
        f"source_path: {_yaml_str('source/email/' + fname)}",
        "---",
        "",
        f"# {subject}",
        "",
        content,
        "",
    ]
    return fname, "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export Mail.app inbox to source/email/")
    ap.add_argument("--limit", type=int, default=300, help="max messages to export per run")
    ap.add_argument("--days", type=int, default=14, help="export messages received within N days")
    args = ap.parse_args()

    EMAIL_SOURCE.mkdir(parents=True, exist_ok=True)
    state = {"seen": []}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    seen = set(state.get("seen", []))

    try:
        msgs = fetch_messages(args.limit, args.days)
    except Exception as e:
        print(f"[mail-export] ERROR: {e}", file=sys.stderr)
        return 2

    new_count = 0
    for msg in msgs:
        mid = msg.get("id") or ""
        if not mid or mid in seen:
            continue
        fname, content = make_note(msg)
        # Legacy notes predate id tracking — skip if same subject already saved
        # within ±1 day (legacy filenames used local dates, allow for tz skew)
        from datetime import date as _date, timedelta as _td
        base = _date.fromisoformat(fname[:10])
        subj_part = re.sub(r"[^\w가-힣]", "", (msg.get("subject") or ""))[:20]
        nearby = []
        for delta in (-1, 0, 1):
            nearby.extend(EMAIL_SOURCE.glob(f"{(base + _td(days=delta)).isoformat()}_*.md"))
        if subj_part and any(
            re.sub(r"[^\w가-힣]", "", p.stem).find(subj_part) != -1
            for p in nearby if p.name != fname
        ):
            seen.add(mid)
            continue
        (EMAIL_SOURCE / fname).write_text(content, encoding="utf-8")
        seen.add(mid)
        new_count += 1
        print(f"[mail-export] {fname}")

    state["seen"] = list(seen)
    state["updated_at"] = datetime.now().isoformat()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[mail-export] done: scanned {len(msgs)}, new {new_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
