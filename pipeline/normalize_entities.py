#!/usr/bin/env python3
"""
normalize_entities.py — 자동추출 엔티티/토픽 정제 (위원회 P2).

문제: frontmatter `entities:` 에 조사·일반명사·형태소(안의, 루트는, 내가, 그리고…)가
엔티티로 잘못 등록됨(약 1,000건). `topics:` 에는 대소문자만 다른 중복(Ontology/ontology).

조치(값 기반·고정밀):
  - 한글 전용 토큰이 조사/어미로 끝나거나 불용어이면 엔티티에서 제거
  - 실제 이름(people/group 노트명·별칭)·라틴/숫자 포함 토큰은 보호(whitelist)
  - 토픽은 대소문자 정규화로 최빈 표기에 병합
파일을 직접 수정하지 않는 dry-run 기본. --apply 시 frontmatter 갱신 + 제거 로그.

사용법:
  python3 scripts/normalize_entities.py                # dry-run + 리포트
  python3 scripts/normalize_entities.py --apply
"""
import os, re, sys, json, argparse
from pathlib import Path
from collections import Counter

import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1])); from kitlib.config import vault_path as _vp; VAULT = _vp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kitlib"))
import vault  # _iter_notes, _note_from_file, _parse_frontmatter 재사용

# 조사/어미 (다중 글자 = 고신뢰)
END_MULTI = ('에서', '으로', '에게', '부터', '까지', '한테', '에서의', '과의', '으로서',
             '으로써', '이라는', '라는', '이라고', '라고', '에서는', '에게서', '으로는')
# 동사/형용사 활용 어미
END_VERB = ('하다', '한다', '하는', '하면', '하고', '했다', '됐다', '되는', '된다', '있는',
            '없는', '없이', '같은', '위한', '대한', '통한', '관한', '라며', '면서', '지만',
            '거나', '어서', '아서', '려고', '으며', '으면', '보고', '보다', '드는', '주는')
# 단일 글자 조사 (len>=3 일 때만 적용해 회의/강의 등 오탐 방지)
END_SINGLE = ('을', '를', '은', '는', '이', '가', '과', '와', '도', '만', '의', '에', '로', '며', '고', '서')
# 명시 불용어 (관찰된 2글자 조각 + 일반 접속/대명사/추상명사)
STOPWORDS = {
    '안의', '내가', '나는', '나의', '제가', '뭐가', '내게', '네가', '우린', '저는', '걔가',
    '그리고', '그래서', '하지만', '그러나', '또한', '즉', '및', '등', '예를', '대해', '대해서',
    '위해', '위해서', '통해', '관련', '관련된', '우리', '너무', '정말', '다시', '지금', '이제',
    '그냥', '바로', '다음', '이번', '각각', '모든', '어떤', '무슨', '무엇', '이런', '그런', '저런',
    '여기', '거기', '저기', '이것', '그것', '저것', '때문', '경우', '정도', '부분', '상태', '방법',
    '문제', '내용', '진행', '확인', '사용', '생성', '설정', '이유', '의미', '중인', '뭔가', '어디',
    '언제', '누가', '왜', '어떻게', '있다', '없다', '한번', '이거', '그거', '저거', '한게', '하게',
}


def is_junk(tok: str, whitelist: set) -> bool:
    t = tok.strip()
    if not t or t in whitelist:
        return False
    # 라틴/숫자 포함 = 보호 (API, iOS, EX1, GPT-4 …)
    if re.search(r'[A-Za-z0-9]', t):
        return False
    # 한글 전용만 대상
    if not re.fullmatch(r'[가-힣]+', t):
        return False
    if len(t) <= 1:
        return True
    if t in STOPWORDS:
        return True
    if t.endswith(END_MULTI) or t.endswith(END_VERB):
        return True
    if len(t) >= 3 and t.endswith(END_SINGLE):
        return True
    return False


def build_whitelist() -> set:
    """people/group 노트의 name·aliases·entities 는 실제 엔티티이므로 보호."""
    wl = set()
    for folder in ("people", "projects"):
        for fp in (VAULT / folder).rglob("*.md"):
            if any(p in ("_templates", "_merged") for p in fp.parts):
                continue
            try:
                _, _ = None, None
                fm, _b = vault._parse_frontmatter(fp.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            for key in ("name", "title"):
                v = fm.get(key)
                if isinstance(v, str) and v.strip():
                    wl.add(v.strip().strip('"'))
            # 실제 식별자만 보호. `entities` 는 자동추출 정크가 섞여 있어 제외(self-poisoning 방지).
            for key in ("aliases", "members"):
                v = fm.get(key)
                if isinstance(v, list):
                    wl.update(x.strip() for x in v if isinstance(x, str) and x.strip())
    return wl


def _rewrite_list_field(text: str, field: str, new_vals: list) -> str:
    """frontmatter 의 `field: [...]` 한 줄을 새 값으로 치환(단순 인라인 리스트만)."""
    inner = ", ".join(f'"{v}"' for v in new_vals)
    pat = re.compile(rf'(?m)^{field}\s*:\s*\[.*?\]\s*$')
    repl = f'{field}: [{inner}]'
    if pat.search(text):
        return pat.sub(repl, text, count=1)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    whitelist = build_whitelist()
    # 토픽 대소문자 병합 맵 구성
    topic_counts = Counter()
    for fp in vault._iter_notes():
        n = vault._note_from_file(fp)
        for t in n.get("topics", []):
            if t:
                topic_counts[t] += 1
    canon = {}
    by_lower = {}
    for t, c in topic_counts.items():
        by_lower.setdefault(t.lower(), []).append((c, t))
    for low, lst in by_lower.items():
        if len(lst) > 1:
            best = max(lst)[1]  # 최빈(동률 시 사전순) 표기로 통일
            for _, t in lst:
                if t != best:
                    canon[t] = best

    removed = Counter()
    topic_merges = 0
    files_changed = 0
    for fp in vault._iter_notes():
        text = fp.read_text(encoding="utf-8", errors="ignore")
        fm, _ = vault._parse_frontmatter(text)
        ents = fm.get("entities", [])
        tops = fm.get("topics", [])
        if not isinstance(ents, list):
            ents = []
        if not isinstance(tops, list):
            tops = []
        new_ents = [e for e in ents if not is_junk(e, whitelist)]
        new_tops = []
        seen = set()
        for t in tops:
            ct = canon.get(t, t)
            if ct not in seen:
                seen.add(ct)
                new_tops.append(ct)
        for e in ents:
            if e not in new_ents:
                removed[e] += 1
        if new_tops != tops:
            topic_merges += 1

        if new_ents != ents or new_tops != tops:
            files_changed += 1
            if args.apply:
                if new_ents != ents:
                    text = _rewrite_list_field(text, "entities", new_ents)
                if new_tops != tops:
                    text = _rewrite_list_field(text, "topics", new_tops)
                fp.write_text(text, encoding="utf-8")

    report = {
        "removed_entity_count": sum(removed.values()),
        "unique_removed": len(removed),
        "topic_canonical_map": canon,
        "files_changed": files_changed,
        "top_removed": removed.most_common(40),
    }
    out = VAULT / "policies" / "entity-normalization-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[normalize_entities] {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"  제거 엔티티: {sum(removed.values())}건 (고유 {len(removed)}종)")
    print(f"  토픽 병합 맵: {canon}")
    print(f"  변경 파일: {files_changed}")
    print(f"  상위 제거 예: {[e for e,_ in removed.most_common(20)]}")
    print(f"  리포트: {out}")


if __name__ == "__main__":
    main()
