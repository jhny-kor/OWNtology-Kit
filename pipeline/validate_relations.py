#!/usr/bin/env python3
"""
validate_relations.py — relations 가 relation.schema.json 표준을 따르는지 검증 (P1).

검사:
  - 필수 필드(subject, predicate, object, status) 존재
  - status enum (confirmed/inferred/proposed/historical/superseded/invalidated)
  - predicate 패턴 [a-z_]+
  - subject/object 가 실재하는 entity_id (참조 무결성)
종료코드: 위반 있으면 1.
"""
import os, re, sys
from collections import defaultdict
from pathlib import Path

import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1])); from kitlib.config import vault_path as _vp; VAULT = _vp()
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_relations_index as B  # parse_relations 재사용

ENTITY_FOLDERS = ("people", "projects", "organizations", "events", "decisions", "preferences")
STATUS = {"confirmed", "inferred", "proposed", "active", "completed", "cancelled", "historical", "superseded", "invalidated"}
PRED_RE = re.compile(r"^[a-z_]+$")


def known_ids():
    ids = set()
    for folder in ENTITY_FOLDERS:
        base = VAULT / folder
        if not base.exists():
            continue
        for fp in base.rglob("*.md"):
            if any(p in ("_templates", "_merged") for p in fp.parts):
                continue
            m = re.search(r"(?m)^entity_id\s*:\s*(\S+)", fp.read_text(encoding="utf-8", errors="ignore"))
            if m:
                ids.add(m.group(1))
    return ids


def main():
    ids = known_ids()
    v = defaultdict(list)
    count = 0
    for folder in ENTITY_FOLDERS:
        base = VAULT / folder
        if not base.exists():
            continue
        for fp in sorted(base.rglob("*.md")):
            if any(p in ("_templates", "_merged") for p in fp.parts):
                continue
            text = fp.read_text(encoding="utf-8", errors="ignore")
            rel = str(fp.relative_to(VAULT))
            # 표준 파서 + status/predicate 원문 점검
            end = text.find("\n---", 3)
            fm = text[4:end] if end != -1 else text
            m = re.search(r"(?m)^relations\s*:\s*$", fm)
            if not m:
                continue
            block = fm[m.end():]
            stop = re.search(r"(?m)^\S", block)
            if stop:
                block = block[:stop.start()]
            for it in B._REL_ITEM.finditer(block):
                body = it.group(1)
                count += 1
                subj = B._field(body, "subject")
                obj = B._field(body, "object")
                pred = B._field(body, "predicate") or B._field(body, "relation")
                status = B._field(body, "status")
                if not (subj and pred and obj):
                    v["missing_required"].append(f"{rel}: {body[:50]}")
                    continue
                if not B._field(body, "predicate"):
                    v["legacy_relation_key"].append(f"{rel}: {pred} (predicate 키 아님)")
                if not status:
                    v["missing_status"].append(f"{rel}: {pred}")
                elif status not in STATUS:
                    v["bad_status"].append(f"{rel}: status={status}")
                if pred and not PRED_RE.match(pred):
                    v["bad_predicate"].append(f"{rel}: {pred}")
                for role, eid in (("subject", subj), ("object", obj)):
                    if eid.startswith(("person:", "group:", "project:", "organization:",
                                        "event:", "decision:", "preference:")) and eid not in ids:
                        v["dangling_ref"].append(f"{rel}: {role}={eid}")

    checks = ["missing_required", "missing_status", "bad_status", "bad_predicate",
              "legacy_relation_key", "dangling_ref"]
    total = sum(len(v[c]) for c in checks)
    print(f"=== validate_relations ({count} relations) ===")
    for c in checks:
        items = v[c]
        print(f"  [{'OK ' if not items else 'FAIL'}] {c}: {len(items)}")
        for it in items[:6]:
            print(f"         - {it}")
    print(f"\n총 위반: {total}건")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
