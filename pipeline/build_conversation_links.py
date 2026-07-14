#!/usr/bin/env python3
"""
build_conversation_links.py — 대화↔엔티티 링크 인덱스 (위원회 강화 A2).

1,876 대화 노트의 `entities`(정제됨)를 canonical entity_id(people/projects/
organizations/...)에 매칭해 indexes/conversation_entities.json 으로 출력한다.
대화 노트 자체는 수정하지 않는다(비침습). 엔티티→대화, 대화→엔티티 양방향 맵을
제공해 'X를 언급한 대화 찾기'(RAG 근거 추적)를 가능케 한다.

  build:  python3 scripts/build_conversation_links.py --build
  query:  python3 scripts/build_conversation_links.py --entity person:kim-jihyeon
"""
import os, sys, json, argparse
from pathlib import Path
from collections import defaultdict

import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1])); from kitlib.config import vault_path as _vp; VAULT = _vp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kitlib"))
import vault

ENTITY_FOLDERS = ("people", "projects", "organizations", "events", "decisions", "preferences")
INDEX = VAULT / "indexes" / "conversation_entities.json"

# 본문 스캔에서 제외할 제네릭 별칭(관계어·일반명사) — 실명만 본문 매칭해 과매칭 방지.
# (frontmatter 매칭에는 계속 사용 — 거긴 구조화 필드라 안전)
_BODY_STOP = {
    "가족", "가족들", "동아리", "친구", "친구들", "아빠", "엄마", "아버지", "어머니",
    "파파", "마마", "여동생", "남동생", "시스타", "형", "누나", "오빠", "언니",
    "본명", "실명", "우리집", "fam", "frontier",
}


def body_name_index():
    """본문 스캔용 인물/그룹 실명 → entity_id. 제네릭 별칭·2글자 미만·숫자 제외.
    people 갭 전용(프로젝트/조직은 frontmatter 매칭이 이미 커버)."""
    idx = {}
    for folder in ("people",):
        base = VAULT / folder
        if not base.exists():
            continue
        for fp in base.rglob("*.md"):
            if any(p in ("_templates", "_merged") for p in fp.parts):
                continue
            fm = vault._note_from_file(fp).get("_fm", {})
            eid = fm.get("entity_id")
            if not eid or fm.get("is_self"):
                continue  # 본인은 거의 모든 대화에 등장 → 색인 무의미
            for c in [fm.get("name", "")] + (fm.get("aliases") or []):
                c = str(c).strip().strip('"')
                if len(c) >= 2 and not c.isdigit() and c.lower() not in _BODY_STOP:
                    idx.setdefault(c.lower(), eid)
    return idx


def name_index():
    """name/alias(lower) -> entity_id. 2글자 미만·순수숫자는 제외(오매칭 방지)."""
    idx = {}
    for folder in ENTITY_FOLDERS:
        base = VAULT / folder
        if not base.exists():
            continue
        for fp in base.rglob("*.md"):
            if any(p in ("_templates", "_merged") for p in fp.parts):
                continue
            fm = vault._note_from_file(fp).get("_fm", {})
            eid = fm.get("entity_id")
            if not eid:
                continue
            cands = [fm.get("name", ""), fm.get("title", "")] + (fm.get("aliases") or [])
            for c in cands:
                c = str(c).strip().strip('"')
                if len(c) >= 2 and not c.isdigit():
                    idx.setdefault(c.lower(), eid)
    return idx


def build():
    nidx = name_index()
    bidx = body_name_index()   # 인물 실명 본문 스캔 인덱스(people 갭 보완)
    by_entity = defaultdict(list)
    by_conv = {}
    convs = 0
    body_hits = 0
    for fp in vault._iter_notes("conversations"):
        n = vault._note_from_file(fp)
        rel = str(fp.relative_to(vault.VAULT_PATH))
        # entities + people + topics + project 필드를 모두 매칭 대상으로
        terms = []
        for field in ("entities", "people", "topics"):
            v = n.get(field, [])
            if isinstance(v, list):
                terms += [x for x in v if isinstance(x, str)]
        if isinstance(n.get("project"), str) and n.get("project"):
            terms.append(n["project"])
        matched = {nidx[t.strip().lower()] for t in terms
                   if isinstance(t, str) and t.strip().lower() in nidx}
        # 본문에서 인물 실명 언급 스캔 — frontmatter에 안 잡히는 대화 속 인물 등장 보완
        body = (n.get("_body") or "").lower()
        if body:
            for term, eid in bidx.items():
                if eid not in matched and term in body:
                    matched.add(eid)
                    body_hits += 1
        matched = sorted(matched)
        convs += 1
        if matched:
            by_conv[rel] = matched
            for eid in matched:
                by_entity[eid].append(rel)

    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps({
        "conversations_scanned": convs,
        "linked_conversations": len(by_conv),
        "linked_entities": len(by_entity),
        "by_entity": {k: sorted(v) for k, v in sorted(by_entity.items())},
        "by_conversation": by_conv,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[conv-links] scanned={convs} linked_convs={len(by_conv)} "
          f"entities_with_convs={len(by_entity)} body_person_hits={body_hits} "
          f"-> {INDEX.relative_to(VAULT)}")
    top = sorted(by_entity.items(), key=lambda x: -len(x[1]))[:8]
    for eid, cs in top:
        print(f"  {eid}: {len(cs)} 대화")


def query(entity):
    if not INDEX.exists():
        build()
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    cs = data["by_entity"].get(entity, [])
    print(f"=== {entity}: {len(cs)} 대화 ===")
    for c in cs[:15]:
        print(f"  - {c}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--entity", default=None)
    args = ap.parse_args()
    if args.entity:
        query(args.entity)
    else:
        build()


if __name__ == "__main__":
    main()
