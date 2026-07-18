#!/usr/bin/env python3
"""
migrate_relations.py — 기존 relations 블록을 relation.schema.json 표준으로 승격 (P1).

frontmatter `relations:` 의 각 항목(`- { subject, relation, object, confidence }`)을
`- { id, subject, predicate, object, valid_from, valid_to, confidence, status, sources }`
플로우 형식으로 재작성한다. status/sources 는 노트의 extraction/source_ids 에서 도출
(extraction confirmed → status confirmed, 아니면 inferred). idempotent(이미 predicate+status면 건너뜀).

  python3 scripts/migrate_relations.py            # dry-run
  python3 scripts/migrate_relations.py --apply
"""
import re, sys, argparse

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()
FOLDERS = ["people", "projects", "organizations", "events", "decisions", "preferences"]
_ITEM = re.compile(r"-\s*\{([^}]*)\}")


def _f(body, *keys):
    for k in keys:
        m = re.search(rf'\b{k}\s*:\s*"?([^",}}\]]+)"?', body)
        if m:
            return m.group(1).strip()
    return None


def _slug(eid):
    return re.sub(r"[^a-z0-9가-힣._-]", "", str(eid).split(":")[-1].lower())


def _fm_field(fm_txt, key):
    m = re.search(rf"(?m)^{key}\s*:\s*(.+)$", fm_txt)
    return m.group(1).strip() if m else None


def migrate_block(fm_txt: str, note_extraction: str, note_sources: str, note_valid_from: str):
    m = re.search(r"(?m)^relations\s*:\s*$", fm_txt)
    if not m:
        return fm_txt, 0
    start = m.start()
    after = fm_txt[m.end():]
    stop = re.search(r"(?m)^\S", after)  # 다음 최상위 키(들여쓰기 0)
    block = after[:stop.start()] if stop else after
    tail = after[stop.start():] if stop else ""

    new_items, changed = [], 0
    for it in _ITEM.finditer(block):
        b = it.group(1)
        subj = _f(b, "subject"); obj = _f(b, "object")
        pred = _f(b, "predicate", "relation")
        if not (subj and obj and pred):
            new_items.append("  - { " + b.strip() + " }")  # 파싱 실패시 원본 보존
            continue
        if _f(b, "predicate") and _f(b, "status"):
            new_items.append("  - { " + b.strip() + " }")  # 이미 표준
            continue
        conf = _f(b, "confidence") or "0.8"
        status = "confirmed" if note_extraction == "confirmed" else "inferred"
        vf = _f(b, "valid_from") or (note_valid_from or "null")
        vt = _f(b, "valid_to") or "null"
        src = note_sources or "source:user-confirmation"
        rid = f"relation:{_slug(subj)}-{pred}-{_slug(obj)}"
        new_items.append(
            f'  - {{ id: {rid}, subject: "{subj}", predicate: {pred}, object: "{obj}", '
            f'valid_from: {vf}, valid_to: {vt}, confidence: {conf}, status: {status}, sources: ["{src}"] }}')
        changed += 1

    if not changed:
        return fm_txt, 0
    new_block = "relations:\n" + "\n".join(new_items) + "\n"
    return fm_txt[:start] + new_block + tail, changed


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    total = 0
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
            fm_txt = text[4:end]
            extraction = (_fm_field(fm_txt, "extraction") or "").strip()
            sources = None
            sm = re.search(r'(?m)^source_ids\s*:\s*\[\s*"?([^",\]]+)', fm_txt)
            if sm:
                sources = sm.group(1).strip()
            elif _fm_field(fm_txt, "source_path"):
                sources = _fm_field(fm_txt, "source_path").strip('"')
            vf = _fm_field(fm_txt, "valid_from")
            new_fm, n = migrate_block(fm_txt, extraction, sources, vf)
            if n:
                total += n
                print(f"  {'MIGRATE' if args.apply else 'DRY '} {fp.relative_to(VAULT)}  ({n} relations)")
                if args.apply:
                    fp.write_text(text[:4] + new_fm + text[end:], encoding="utf-8")
    print(f"[migrate_relations] {'APPLY' if args.apply else 'DRY-RUN'} — {total} relations upgraded")


if __name__ == "__main__":
    main()
