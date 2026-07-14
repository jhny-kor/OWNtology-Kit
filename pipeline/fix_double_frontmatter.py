#!/usr/bin/env python3
"""
fix_double_frontmatter.py — 이중 frontmatter 노트를 단일 블록으로 병합 (위원회 P1).

enrich 단계에서 canonical 블록과 enrich 블록이 둘 다 남은 케이스. 더 풍부한
첫 블록을 유지하고, 두 번째 블록은 제거한 뒤 그 이후의 본문을 보존한다.
idempotent — 이중 블록이 아닌 파일은 건드리지 않는다.

사용법:
  python3 scripts/fix_double_frontmatter.py            # dry-run
  python3 scripts/fix_double_frontmatter.py --apply
"""
import os, sys, argparse
from pathlib import Path

import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1])); from kitlib.config import vault_path as _vp; VAULT = _vp()
SKIP_DIRS = {".obsidian", ".omc", ".omx", "opencrab_data", "__pycache__",
             ".git", "quarantine"}


def split_double(text: str):
    """이중 frontmatter면 (merged_text) 반환, 아니면 None."""
    if not text.startswith("---"):
        return None
    end1 = text.find("\n---", 3)
    if end1 == -1:
        return None
    block1 = text[:end1 + 4]          # 첫 블록 전체(닫는 --- 포함)
    rest = text[end1 + 4:]
    stripped = rest.lstrip("\n")
    if not stripped.startswith("---"):
        return None                   # 이중 아님
    # 두 번째 블록의 닫는 펜스 찾기
    end2 = stripped.find("\n---", 3)
    if end2 == -1:
        return None
    body = stripped[end2 + 4:]
    return block1.rstrip() + "\n" + body.lstrip("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    fixed = 0
    for fp in VAULT.rglob("*.md"):
        if any(p in SKIP_DIRS for p in fp.parts):
            continue
        text = fp.read_text(encoding="utf-8", errors="ignore")
        merged = split_double(text)
        if merged is None:
            continue
        print(f"  {'FIX ' if args.apply else 'DRY '} {fp.relative_to(VAULT)}")
        if args.apply:
            fp.write_text(merged, encoding="utf-8")
        fixed += 1
    print(f"[fix_double_frontmatter] {fixed}건 {'수정' if args.apply else 'dry-run'}")


if __name__ == "__main__":
    main()
