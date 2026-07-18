#!/usr/bin/env python3
"""
create_member_stubs.py — 그룹 구성원에 대한 최소 person 엔티티 생성 (위원회 Track B #2).

group 노트의 members(평문 이름)에 대해, 아직 person 노트가 없는 사람의 **최소 스텁**을
만든다. 타인 PII 최소화 원칙(위원회): 이름 + 소속 그룹(member_of) + sensitivity만 담고
대화/메시지는 적재하지 않는다. extraction=auto, confidence 낮게. idempotent, 되돌리기 쉬움
(생성 파일 목록을 policies/member-stubs-created.json 에 기록).

사용법:
  python3 scripts/create_member_stubs.py            # dry-run
  python3 scripts/create_member_stubs.py --apply
"""
import sys, re, json, argparse
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kitlib"))
import vault

PEOPLE = VAULT / "people"


def existing_names() -> dict:
    """name/alias -> entity_id (이미 존재하는 person/group)."""
    idx = {}
    for fp in PEOPLE.rglob("*.md"):
        if any(p in ("_templates", "_merged") for p in fp.parts):
            continue
        fm = vault._note_from_file(fp).get("_fm", {})
        eid = fm.get("entity_id", "")
        for key in ("name", "title"):
            v = fm.get(key)
            if isinstance(v, str) and v.strip():
                idx[v.strip().strip('"')] = eid
        for a in (fm.get("aliases") or []):
            if isinstance(a, str):
                idx[a.strip()] = eid
    return idx


def _person_stub(name: str, relationship: str, phone: str, source_id: str) -> str:
    """수동입력 필드(relationship·phone·aliases)는 빈 값으로 생성 — 웹 화면에서 채움."""
    eid = f"person:{name}"
    return (
        f"---\n"
        f"type: person\n"
        f"entity_id: {eid}\n"
        f"canonical: true\n"
        f"name: {name}\n"
        f"relationship: {json.dumps(relationship, ensure_ascii=False)}\n"
        f"phone: {json.dumps(phone, ensure_ascii=False)}\n"
        f"aliases: []\n"
        f"tags: [person, stub]\n"
        f"sensitivity: sensitive\n"
        f"source_path: \"people/{name}.md\"\n"
        f"valid_from: {date.today().isoformat()}\n"
        f"valid_to: \n"
        f"confidence: 0.5\n"
        f"extraction: auto\n"
        f"source_ids: [{json.dumps(source_id, ensure_ascii=False)}]\n"
        f"---\n\n"
        f"# {name}\n\n"
        f"> 카카오톡 1:1 대화 상대에서 생성된 최소 스텁(타인 PII 최소화). "
        f"관계·별칭 등은 웹 화면(kit.py web)에서 직접 입력.\n"
    )


def kakao_direct_stubs(known: dict, apply: bool, created: list) -> None:
    """카카오 1:1 대화 상대별 person 스텁 — chat_type: direct 스냅샷 기반."""
    src = VAULT / "source" / "kakao"
    if not src.exists():
        return
    contacts = {}
    cfile = src / ".contacts.json"
    if cfile.exists():
        try:
            contacts = json.loads(cfile.read_text(encoding="utf-8"))
        except Exception:
            contacts = {}
    for fp in sorted(src.glob("kmsg-*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("chat_type") != "direct":
            continue
        name = (data.get("chat") or "").strip()
        if not name or name in known:
            continue
        dest = PEOPLE / re.sub(r'[\\/:*?"<>|]', "_", name)
        dest = dest.with_suffix(".md")
        if dest.exists():
            continue
        created.append({"name": name, "entity_id": f"person:{name}",
                        "group": "", "file": f"people/{dest.name}"})
        print(f"  {'CREATE' if apply else 'DRY '} people/{dest.name}  (kakao 1:1)")
        if apply:
            dest.write_text(
                _person_stub(name, "", contacts.get(name, ""),
                             f"source/kakao/{fp.name}"), encoding="utf-8")
        known[name] = f"person:{name}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    known = existing_names()
    created = []
    kakao_direct_stubs(known, args.apply, created)
    # 그룹 노트 순회
    for fp in PEOPLE.glob("*.md"):
        fm = vault._note_from_file(fp).get("_fm", {})
        if fm.get("type") != "group":
            continue
        gid = fm.get("entity_id", "")
        gname = fm.get("name") or fp.stem
        for m in (fm.get("members") or []):
            if not isinstance(m, str) or not m.strip():
                continue
            m = m.strip()
            if m.startswith(("person:", "group:")):
                continue  # 이미 entity_id 참조 (별도 처리/존재)
            if m in known:
                continue  # 이미 person 노트 있음
            eid = f"person:{m}"
            dest = PEOPLE / f"{m}.md"
            if dest.exists():
                continue
            created.append({"name": m, "entity_id": eid, "group": gid, "file": f"people/{m}.md"})
            print(f"  {'CREATE' if args.apply else 'DRY '} people/{m}.md  ({eid}, member_of {gid})")
            if args.apply:
                body = (
                    f"---\n"
                    f"type: person\n"
                    f"entity_id: {eid}\n"
                    f"canonical: true\n"
                    f"name: {m}\n"
                    f"relationship: {gname} 구성원\n"
                    f"tags: [person, stub]\n"
                    f"member_of: [\"{gid}\"]\n"
                    f"sensitivity: sensitive\n"
                    f"source_path: \"people/{m}.md\"\n"
                    f"valid_from: {date.today().isoformat()}\n"
                    f"valid_to: \n"
                    f"confidence: 0.5\n"
                    f"extraction: auto\n"
                    f"source_ids: [\"people/{fp.stem}.md\"]\n"
                    f"---\n\n"
                    f"# {m}\n\n"
                    f"## 기본 정보\n"
                    f"- 소속: [[{fp.stem}]] ({gid})\n\n"
                    f"> 그룹 구성원 명단에서 생성된 최소 스텁(타인 PII 최소화). "
                    f"메시지/대화는 적재하지 않음. 필요 시 사용자 확인 후 보강.\n"
                )
                known[m] = eid  # 동일 실행 내 중복 방지
                dest.write_text(body, encoding="utf-8")

    if args.apply and created:
        log = VAULT / "policies" / "member-stubs-created.json"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps(created, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[create_member_stubs] {'APPLY' if args.apply else 'DRY-RUN'} — {len(created)} stubs")


if __name__ == "__main__":
    main()
