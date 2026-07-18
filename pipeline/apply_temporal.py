#!/usr/bin/env python3
"""
apply_temporal.py — 엔티티 시점 모델링 (위원회 강화 A3: 현재/과거 구분).

프로젝트의 status 로 현재/과거를 구분해 frontmatter 에 표준화한다:
  - status active  → current: true,  valid_to 비움(현재 유효)
  - status archived/done/unknown → current: false, valid_to=updated(있으면)
관계(가족·연인)는 종료 정보가 없으므로 건드리지 않는다(추측 회피).
idempotent. dry-run 기본.

사용법: python3 scripts/apply_temporal.py --apply
"""
import re, sys, argparse

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kitlib"))
import vault

PAST = {"archived", "done", "unknown", "deprecated"}


def upsert(fm_txt: str, key: str, val: str) -> str:
    line = f"{key}: {val}"
    if re.search(rf"(?m)^{key}\s*:", fm_txt):
        return re.sub(rf"(?m)^{key}\s*:.*$", line, fm_txt, count=1)
    return fm_txt.rstrip() + "\n" + line


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    changed = 0
    for fp in sorted((VAULT / "projects").rglob("*.md")):
        text = fp.read_text(encoding="utf-8", errors="ignore")
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        fm_txt = text[4:end]
        fm = vault._note_from_file(fp).get("_fm", {})
        status = (fm.get("status") or "").lower()
        if not status:
            continue
        is_past = status in PAST
        updated = fm.get("updated") or fm.get("date") or ""
        updated = "" if updated in ("unknown", "") else updated
        want_current = "false" if is_past else "true"
        want_valid_to = updated if (is_past and updated) else ""

        new_fm = upsert(fm_txt, "current", want_current)
        new_fm = upsert(new_fm, "valid_to", want_valid_to)
        if new_fm == fm_txt:
            continue
        changed += 1
        print(f"  {'SET ' if args.apply else 'DRY '} {fp.stem:24} current={want_current} valid_to={want_valid_to!r} (status={status})")
        if args.apply:
            fp.write_text(text[:4] + new_fm + text[end:], encoding="utf-8")
    print(f"[apply_temporal] {'APPLY' if args.apply else 'DRY-RUN'} — {changed} projects")


if __name__ == "__main__":
    main()
