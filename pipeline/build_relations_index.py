#!/usr/bin/env python3
"""
build_relations_index.py — 구조화 관계를 그래프 인덱스로 집계 + 조회 (위원회 Track B #4).

엔티티 노트(people/projects/organizations/...)의 frontmatter에서 관계를 모아
indexes/relations.json 으로 출력한다. 본문 자연어가 아니라 frontmatter 의 구조화
필드만 사용한다(relations / member_of / members / works_on).

  build:  python3 scripts/build_relations_index.py --build
  query:  python3 scripts/build_relations_index.py --entity person:kim-jihyeon
"""
import re, sys, json, argparse

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kitlib"))
import vault

# PyYAML 부재(시스템 python) 대비, relations 플로우 블록을 경량 파싱한다.
_REL_ITEM = re.compile(r"-\s*\{([^}]*)\}")
def _field(s, key):
    m = re.search(rf'{key}\s*:\s*"?([^",}}\]]+)"?', s)
    return m.group(1).strip() if m else None


def _clean(v):
    return "" if (v or "").strip().lower() in ("null", "none", "~") else (v or "").strip()


def parse_relations(text: str) -> list:
    """frontmatter 의 `relations:` 블록에서 {subject,relation,object,confidence} 추출."""
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    fm = text[4:end] if end != -1 else text
    m = re.search(r"(?m)^relations\s*:\s*$", fm)
    if not m:
        return []
    block = fm[m.end():]
    # 다음 최상위 키(들여쓰기 없는 'key:')에서 멈춤
    stop = re.search(r"(?m)^\S+\s*:", block)
    if stop:
        block = block[:stop.start()]
    out = []
    for item in _REL_ITEM.finditer(block):
        body = item.group(1)
        subj = _field(body, "subject")
        obj = _field(body, "object")
        rel = _field(body, "predicate") or _field(body, "relation")  # 표준=predicate
        if subj and obj:
            out.append({"subject": subj, "relation": rel or "related_to", "object": obj,
                        "confidence": _field(body, "confidence") or "",
                        "status": _field(body, "status") or "",
                        "valid_from": _clean(_field(body, "valid_from")),
                        "valid_to": _clean(_field(body, "valid_to"))})
    return out

ENTITY_FOLDERS = ("people", "projects", "organizations", "events", "decisions", "preferences")
INDEX = VAULT / "indexes" / "relations.json"


def collect():
    nodes = {}        # entity_id -> {type, name, path}
    name2id = {}      # name/alias -> entity_id
    raw = []          # (subj, rel, obj, confidence, status, sources, valid_from, valid_to)

    for folder in ENTITY_FOLDERS:
        base = VAULT / folder
        if not base.exists():
            continue
        for fp in base.rglob("*.md"):
            if any(p in ("_templates", "_merged") for p in fp.parts):
                continue
            n = vault._note_from_file(fp); fm = n.get("_fm", {})
            eid = fm.get("entity_id")
            if not eid:
                continue
            nm = (fm.get("name") or n.get("title") or fp.stem).strip().strip('"')
            aliases = [a.strip() for a in (fm.get("aliases") or [])
                       if isinstance(a, str) and a.strip()]
            nodes[eid] = {"type": fm.get("type", ""), "name": nm,
                          "aliases": aliases,  # 별칭 조회용(resolver가 name+aliases로 해석)
                          "path": str(fp.relative_to(VAULT))}
            name2id[nm] = eid
            for a in aliases:
                name2id.setdefault(a, eid)

    def resolve(x):
        x = str(x).strip()
        if x.startswith(("person:", "group:", "project:", "organization:",
                          "event:", "decision:", "preference:")):
            return x
        return name2id.get(x, x)  # 미해결 평문이름은 그대로(스텁 전 상태)

    # 2nd pass: edges
    for folder in ENTITY_FOLDERS:
        base = VAULT / folder
        if not base.exists():
            continue
        for fp in base.rglob("*.md"):
            if any(p in ("_templates", "_merged") for p in fp.parts):
                continue
            text = fp.read_text(encoding="utf-8", errors="ignore")
            fm = vault._note_from_file(fp).get("_fm", {})
            eid = fm.get("entity_id")
            if not eid:
                continue
            rel_path = str(fp.relative_to(VAULT))
            # 단축필드 엣지의 근거·생명주기(위원회 P3): 출처는 큐레이션 노트 자신,
            # status=confirmed(사람이 frontmatter에 직접 기재).
            # 시간축(5차 P2): 노트의 valid_from은 관계 시작일이 아니라 기록일이므로
            # asserted_at으로만 싣고, valid_from은 관계별 명시값이 없으면 unknown("").
            asserted = _clean(fm.get("valid_from") or fm.get("verified_at") or "")
            vt = _clean(fm.get("valid_to") or "")
            shorthand = ("0.8", "confirmed", [rel_path], "", vt, asserted)
            for r in parse_relations(text):   # 경량 파서로 relations 블록 추출
                raw.append((resolve(r["subject"]), r["relation"], resolve(r["object"]),
                            r["confidence"], r["status"] or "confirmed", [rel_path],
                            r.get("valid_from") or "", r.get("valid_to") or "", asserted))
            for g in (fm.get("member_of") or []):
                raw.append((eid, "member_of", resolve(g), *shorthand))
            for m in (fm.get("members") or []):
                raw.append((resolve(m), "member_of", eid, *shorthand))
            for p in (fm.get("works_on") or []):
                raw.append((eid, "works_on", resolve(p), *shorthand))
            for d in (fm.get("depends_on") or []):
                raw.append((eid, "depends_on", resolve(d), *shorthand))
            for w in (fm.get("works_at") or []):
                raw.append((eid, "works_at", resolve(w), *shorthand))
            for a in (fm.get("applies_to") or []):   # decision→project 등 적용 대상
                raw.append((eid, "applies_to", resolve(a), *shorthand))

    # 역관계 자동생성 (C3): 명시 엣지의 inverse 를 inferred 로 추가
    INVERSE = {
        "father_of": "child_of", "mother_of": "child_of",
        "younger_sister_of": "older_sibling_of", "older_sibling_of": "younger_sibling_of",
        "spouse_of": "spouse_of",            # 대칭
        "works_at": "employs", "member_of": "has_member",
        "works_on": "contributed_by", "depends_on": "depended_on_by",
        "applies_to": "has_decision",
        "replaced_by": "replaces", "part_of": "has_part",  # 프로젝트 계보(5차 P3)
    }
    # 명시 엣지 먼저 dedupe
    seen = set(); edges = []
    for s, rel, o, c, st, src, vf, vt, at in raw:
        k = (s, rel, o)
        if k in seen:
            continue
        seen.add(k)
        edges.append({"subject": s, "relation": rel, "object": o, "confidence": c,
                      "status": st, "sources": src, "valid_from": vf, "valid_to": vt,
                      "asserted_at": at, "inferred": False})
    # inverse 추가 (이미 명시된 (o, inv, s) 는 건너뜀 — 실데이터 우선)
    for s, rel, o, c, st, src, vf, vt, at in list(raw):
        inv = INVERSE.get(rel)
        if not inv:
            continue
        k = (o, inv, s)
        if k in seen:
            continue
        seen.add(k)
        edges.append({"subject": o, "relation": inv, "object": s, "confidence": c,
                      "status": "inferred", "sources": src, "valid_from": vf, "valid_to": vt,
                      "asserted_at": at, "inferred": True})
    return nodes, edges


def build():
    nodes, edges = collect()
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps({
        "node_count": len(nodes), "edge_count": len(edges),
        "nodes": nodes, "edges": edges,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    rels = {}
    for e in edges:
        rels[e["relation"]] = rels.get(e["relation"], 0) + 1
    print(f"[relations] nodes={len(nodes)} edges={len(edges)} -> {INDEX.relative_to(VAULT)}")
    print(f"  by relation: {rels}")


def query(entity):
    if not INDEX.exists():
        build()
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    nm = {k: v["name"] for k, v in data["nodes"].items()}
    print(f"=== {entity} ({nm.get(entity,'?')}) ===")
    out = [e for e in data["edges"] if e["subject"] == entity]
    inc = [e for e in data["edges"] if e["object"] == entity]
    for e in out:
        print(f"  -> {e['relation']:18} {e['object']} ({nm.get(e['object'],'?')})")
    for e in inc:
        print(f"  <- {e['relation']:18} {e['subject']} ({nm.get(e['subject'],'?')})")
    if not out and not inc:
        print("  (관계 없음)")


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
