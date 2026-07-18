#!/usr/bin/env python3
"""
assign_entity_ids.py — person/group/project 노트에 불변 entity_id 부여 (위원회 P1-2).

이미 entity_id가 있으면 건드리지 않는다(idempotent). 없으면 type+slug로 생성해
frontmatter의 type 다음 줄에 삽입한다. slug는 name 또는 파일명 기반.

사용법:
  python3 scripts/assign_entity_ids.py            # dry-run
  python3 scripts/assign_entity_ids.py --apply
"""

import re, sys, argparse, unicodedata

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()
TARGET_FOLDERS = ["people", "projects"]
SKIP_DIRS = {"_templates", "_merged", "quarantine"}

# 이름 → 슬러그 수동 지정(config.json entity_slugs). 없으면 원문 슬러그 유지
# (스키마가 한글 슬러그 허용: ^[a-z]+:[a-z0-9가-힣._-]+$).
from kitlib.config import load as _load_cfg
_HANGUL_HINTS = _load_cfg().get("entity_slugs", {})


def _slug(name: str, stem: str) -> str:
    base = (name or stem or "").strip().strip('"')
    if base in _HANGUL_HINTS:
        return _HANGUL_HINTS[base]
    # 날짜 접두사 제거
    base = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", base)
    base = unicodedata.normalize("NFKC", base).lower()
    base = re.sub(r"[^\w가-힣-]+", "-", base).strip("-")
    return base[:40] or "unknown"


def _read_fm_type_name(text: str):
    if not text.startswith("---"):
        return None, None, None
    end = text.find("\n---", 3)
    if end == -1:
        return None, None, None
    fm = text[4:end]
    has_id = re.search(r"^entity_id\s*:", fm, re.MULTILINE) is not None
    t = re.search(r"^type\s*:\s*(\S+)", fm, re.MULTILINE)
    n = re.search(r'^name\s*:\s*"?([^"\n]+)"?', fm, re.MULTILINE)
    if not n:
        n = re.search(r'^title\s*:\s*"?([^"\n]+)"?', fm, re.MULTILINE)
    return (t.group(1) if t else None), (n.group(1).strip() if n else None), has_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    changed = 0
    for folder in TARGET_FOLDERS:
        base = VAULT / folder
        if not base.exists():
            continue
        for fp in sorted(base.rglob("*.md")):
            if any(p in SKIP_DIRS for p in fp.parts):
                continue
            text = fp.read_text(encoding="utf-8", errors="ignore")
            etype, name, has_id = _read_fm_type_name(text)
            if has_id or etype is None:
                continue
            etype = etype if etype in {"person", "group", "project", "organization"} else \
                ("project" if folder == "projects" else "person")
            eid = f"{etype}:{_slug(name, fp.stem)}"
            print(f"  {'SET ' if args.apply else 'DRY '} {fp.relative_to(VAULT)}  ->  entity_id: {eid}")
            if args.apply:
                new = re.sub(r"(^type\s*:.*$)", r"\1\nentity_id: " + eid,
                             text, count=1, flags=re.MULTILINE)
                fp.write_text(new, encoding="utf-8")
            changed += 1

    print(f"[assign_entity_ids] {changed}건 {'적용' if args.apply else 'dry-run'}")


if __name__ == "__main__":
    main()
