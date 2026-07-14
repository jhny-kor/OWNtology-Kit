#!/usr/bin/env python3
"""
validate_ontology.py — 위원회 '데이터 품질 검증' 자동 검사 (목표: 위반 0건).

검사 항목:
  1. 한 파일에 frontmatter 1개 (이중 frontmatter 0)
  2. type: person 인데 그룹(tags/members에 group 단서) 0
  3. people 검색공간에 template 0 (_templates/ 로 분리됐는지)
  4. person/group/project 노트 entity_id 누락 0
  5. 동일 entity_id 중복 0
  6. canonical 동일 엔티티 중복 0 (entity_id별 canonical 1개 이하)
  7. source/email 와 conversations/email basename 중복 0

종료코드: 위반 있으면 1.
사용법: python3 scripts/validate_ontology.py
"""

import os, re, sys
from collections import defaultdict
from pathlib import Path

import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1])); from kitlib.config import vault_path as _vp; VAULT = _vp()
SKIP_DIRS = {"_templates", "_merged", "quarantine", "_summaries", ".obsidian",
             ".omc", ".omx", "opencrab_data", "__pycache__", ".git"}

# ontology.schema.json 과 동기 — 스키마 준수 검사용
VALID_TYPES = {"person", "group", "project", "organization", "account",
               "event", "conversation", "note", "decision", "preference",
               "index", "blog", "daily", "link", "link_batch", "catalog_update"}
ENTITY_ID_RE = re.compile(r"^[a-z]+:[a-z0-9가-힣._-]+$")


def fm_block(text: str):
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    return text[4:end] if end != -1 else None


def is_double_frontmatter(text: str) -> bool:
    """첫 frontmatter 블록이 닫힌 직후(빈 줄 허용) 또 다른 '---' 펜스가 오면 이중.
    본문 중간의 '---' 수평선(hr)은 앞에 내용이 있으므로 오탐하지 않는다."""
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    rest = text[end + 4:]            # 첫 블록 닫는 '---' 다음
    rest = rest.lstrip("\n").lstrip()
    return rest.startswith("---")


def iter_md(folder):
    for fp in (VAULT / folder).rglob("*.md"):
        if any(p in SKIP_DIRS for p in fp.parts):
            continue
        yield fp


def main():
    violations = defaultdict(list)
    id_seen = defaultdict(list)
    canonical_seen = defaultdict(list)
    refs = []  # (source_rel, ref_entity_id) — works_on/member_of/members/relations 의 typed 참조

    REF_RE = re.compile(r"\b(?:person|group|project|organization|event|account|decision|preference):[a-z0-9가-힣._-]+")

    entity_folders = ("people", "projects", "organizations", "events", "decisions", "preferences")
    for folder in ["people", "projects", "organizations", "events", "decisions",
                   "preferences", "conversations", "knowledge", "daily"]:
        if not (VAULT / folder).exists():
            continue
        for fp in iter_md(folder):
            rel = str(fp.relative_to(VAULT))
            text = fp.read_text(encoding="utf-8", errors="ignore")
            fm = fm_block(text) or ""

            # 1. 이중 frontmatter
            if is_double_frontmatter(text):
                violations["double_frontmatter"].append(rel)

            mtype = re.search(r"^type\s*:\s*(\S+)", fm, re.MULTILINE)
            mtype = mtype.group(1) if mtype else None

            # 0. 스키마 type enum 준수 (ontology.schema.json)
            if mtype and mtype not in VALID_TYPES:
                violations["invalid_type"].append(f"{rel}: type={mtype}")

            # 2. person 인데 그룹
            if mtype == "person":
                if re.search(r"members\s*:", fm) or re.search(r'tags\s*:.*group', fm) \
                   or re.search(r'relationship\s*:\s*(가족|친구들|동아리)', fm):
                    violations["person_is_group"].append(rel)

            # 3. template 인물 색인
            if folder == "people" and ("template" in fp.name.lower() or "{{name}}" in text):
                violations["template_indexed"].append(rel)

            # 4/5/6. entity_id
            if folder in entity_folders and mtype in ("person", "group", "project",
                    "organization", "event", "decision", "preference"):
                mid = re.search(r"^entity_id\s*:\s*(\S+)", fm, re.MULTILINE)
                if not mid:
                    violations["missing_entity_id"].append(rel)
                else:
                    eid = mid.group(1)
                    id_seen[eid].append(rel)
                    # 스키마 entity_id 형식: '<type>:<slug>'
                    if not ENTITY_ID_RE.match(eid):
                        violations["bad_entity_id_format"].append(f"{rel}: {eid}")
                    if re.search(r"^canonical\s*:\s*true", fm, re.MULTILINE):
                        canonical_seen[eid].append(rel)

            # 참조 무결성: works_on/member_of/members/relations 의 typed 참조 수집
            if folder in entity_folders:
                for line in fm.splitlines():
                    if re.match(r"^\s*(works_on|member_of|members|relations|subject|object|depends_on|works_at)\s*:", line) \
                       or re.match(r"^\s*-\s", line) or "{" in line:
                        for m in REF_RE.finditer(line):
                            refs.append((rel, m.group(0)))

    for eid, paths in id_seen.items():
        if len(paths) > 1:
            violations["duplicate_entity_id"].append(f"{eid}: {paths}")
    for eid, paths in canonical_seen.items():
        if len(paths) > 1:
            violations["duplicate_canonical"].append(f"{eid}: {paths}")

    # 참조 무결성: typed 참조가 실재하는 entity_id 를 가리키는가 (존재하지 않는 링크 0건)
    known_ids = set(id_seen.keys())
    dangling = sorted({ref for _, ref in refs if ref not in known_ids})
    for d in dangling:
        srcs = sorted({s for s, r in refs if r == d})
        violations["dangling_entity_ref"].append(f"{d}  <- {srcs[:3]}")

    # 7. 이메일 원본/요약 중복
    src = {p.name for p in (VAULT / "source" / "email").glob("*.md")} if (VAULT / "source" / "email").exists() else set()
    conv = {p.name for p in (VAULT / "conversations" / "email").glob("*.md")} if (VAULT / "conversations" / "email").exists() else set()
    dupes = src & conv
    if dupes:
        violations["email_duplicate"].append(f"{len(dupes)}건 중복 (예: {sorted(dupes)[:3]})")

    total = sum(len(v) for v in violations.values())
    print("=== validate_ontology ===")
    checks = ["double_frontmatter", "person_is_group", "template_indexed",
              "missing_entity_id", "bad_entity_id_format", "invalid_type",
              "duplicate_entity_id", "duplicate_canonical", "email_duplicate",
              "dangling_entity_ref"]
    for c in checks:
        items = violations.get(c, [])
        mark = "OK " if not items else "FAIL"
        print(f"  [{mark}] {c}: {len(items)}")
        for it in items[:8]:
            print(f"         - {it}")
    print(f"\n총 위반: {total}건")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
