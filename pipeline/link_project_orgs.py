#!/usr/bin/env python3
"""
link_project_orgs.py — 프로젝트→조직 의존 관계 추가 (위원회 강화 A1).

organizations/ 엔티티의 name/alias 를 프로젝트 노트의 tech/본문에서 탐지해
frontmatter 에 `depends_on: ["organization:..."]`(인라인 리스트) 를 추가한다.
build_relations_index 가 이를 'project depends_on organization' 엣지로 집계한다.
idempotent (이미 있으면 병합), dry-run 기본.

사용법: python3 scripts/link_project_orgs.py --apply
"""
import re, sys, argparse

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kitlib"))
import vault


def load_orgs():
    """[(entity_id, [match_terms...])] — 이름+별칭으로 매칭."""
    orgs = []
    for fp in (VAULT / "organizations").glob("*.md"):
        fm = vault._note_from_file(fp).get("_fm", {})
        eid = fm.get("entity_id")
        if not eid:
            continue
        terms = [fm.get("name", "")] + (fm.get("aliases") or [])
        terms = [t for t in terms if t and len(t) >= 2]
        orgs.append((eid, terms))
    return orgs


def detect(text, orgs):
    found = []
    low = text.lower()
    for eid, terms in orgs:
        if any(t.lower() in low for t in terms):
            found.append(eid)
    return sorted(set(found))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    orgs = load_orgs()
    changed = 0
    for fp in sorted((VAULT / "projects").rglob("*.md")):
        text = fp.read_text(encoding="utf-8", errors="ignore")
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        fm_txt = text[4:end] if end != -1 else ""
        # 고신호 소스만: tech 필드(실제 의존성) + category 규칙. 본문은 오탐多 → 제외.
        tech_line = "\n".join(l for l in fm_txt.splitlines()
                              if l.startswith(("tech:", "platform:")))
        deps = set(detect(tech_line, orgs))
        cat_m = re.search(r"(?m)^category\s*:\s*(\S+)", fm_txt)
        cat = (cat_m.group(1) if cat_m else "").lower()
        if "ios" in cat:                       # iOS 앱 → App Store(Apple)
            deps.add("organization:apple")
        if "github" in cat or fp.stem.endswith(("github-io", "github-io")):
            deps.add("organization:github")
        deps = sorted(deps)
        # 자기 자신이 org가 아니므로 그대로
        existing = re.search(r"(?m)^depends_on\s*:\s*\[(.*?)\]", fm_txt)
        cur = set(re.findall(r'organization:[a-z0-9가-힣._-]+', existing.group(1))) if existing else set()
        merged = sorted(cur | set(deps))
        if not merged or merged == sorted(cur):
            continue
        changed += 1
        line = "depends_on: [" + ", ".join(f'"{d}"' for d in merged) + "]"
        print(f"  {'SET ' if args.apply else 'DRY '} {fp.name:28} {merged}")
        if args.apply:
            if existing:
                new = re.sub(r"(?m)^depends_on\s*:.*$", line, text, count=1)
            else:
                new = text[:end + 1] + line + "\n" + text[end + 1:]
            fp.write_text(new, encoding="utf-8")
    print(f"[link_project_orgs] {'APPLY' if args.apply else 'DRY-RUN'} — {changed} projects")


if __name__ == "__main__":
    main()
