#!/usr/bin/env python3
"""
apply_fact_status.py — 엔티티 노트에 사실 상태 표준화 (위원회 재평가 P2).

preferences/decisions/events/organizations/people 노트에 `status`(confirmed/inferred/
proposed/historical/superseded) 필드를 추가한다. 기존 `extraction`(auto/confirmed)에서
도출: confirmed→status confirmed(+verified_at), auto/none→inferred. confidence·valid_from·
valid_to·sources 가 없으면 보강. projects 는 status(active/archived=lifecycle)와 충돌하므로 제외.
idempotent.

  python3 scripts/apply_fact_status.py            # dry-run
  python3 scripts/apply_fact_status.py --apply
"""
import os, re, sys, argparse
from datetime import date
from pathlib import Path

import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1])); from kitlib.config import vault_path as _vp; VAULT = _vp()
FOLDERS = ["preferences", "decisions", "events", "organizations", "people"]
TODAY = date(2026, 6, 22).isoformat()


def _get(fm, key):
    m = re.search(rf"(?m)^{key}\s*:\s*(.*)$", fm)
    return m.group(1).strip() if m else None


def _upsert(fm, key, val):
    if re.search(rf"(?m)^{key}\s*:", fm):
        return re.sub(rf"(?m)^{key}\s*:.*$", f"{key}: {val}", fm, count=1)
    return fm.rstrip("\n") + f"\n{key}: {val}\n"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    changed = 0
    for folder in FOLDERS:
        base = VAULT / folder
        if not base.exists():
            continue
        for fp in sorted(base.rglob("*.md")):
            if any(p in ("_templates", "_merged") for p in fp.parts):
                continue
            text = fp.read_text(encoding="utf-8", errors="ignore")
            if not text.startswith("---"):
                continue
            end = text.find("\n---", 3)
            fm = text[4:end]
            if _get(fm, "status") in ("confirmed", "inferred", "proposed", "historical", "superseded"):
                continue  # 이미 표준 상태
            extraction = (_get(fm, "extraction") or "").strip()
            status = "confirmed" if extraction == "confirmed" else "inferred"
            new = _upsert(fm, "status", status)
            if status == "confirmed" and not _get(fm, "verified_at"):
                new = _upsert(new, "verified_at", _get(fm, "valid_from") or TODAY)
            if not _get(fm, "confidence"):
                new = _upsert(new, "confidence", "0.6")
            if not _get(fm, "sources") and not _get(fm, "source_ids"):
                sp = _get(fm, "source_path")
                new = _upsert(new, "sources", f'["{sp.strip(chr(34))}"]' if sp else '["derived"]')
            if new != fm:
                changed += 1
                if changed <= 8:
                    print(f"  {'SET ' if args.apply else 'DRY '} {fp.relative_to(VAULT)}  status={status}")
                if args.apply:
                    fp.write_text(text[:4] + new + text[end:], encoding="utf-8")
    print(f"[apply_fact_status] {'APPLY' if args.apply else 'DRY-RUN'} — {changed} notes")


if __name__ == "__main__":
    main()
