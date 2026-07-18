#!/usr/bin/env python3
"""일일 롤업 생성 (로드맵 item 6). daily/ 시계열 축 활성화.

최근 N일(기본 14)의 **본인 대화**를 하루 단위로 집계해 daily/YYYY-MM-DD.md 를 만든다.
대화 노트가 이미 날짜별 시계열이므로 과거 전체를 bulk 생성하지 않고 최근 창만 갱신
(과잉수집 방지). 본인 데이터만 다루므로 민감도 이슈 없음. 자동생성 노트만 덮어쓰고
사용자가 손댄 daily 노트는 보존(generated: true 마커로 구분).

실행:
  python3 daily_rollup.py                 # dry-run, 최근 14일
  python3 daily_rollup.py --apply --days 14
"""
import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp  # noqa: E402
_vp()  # OWNTOLOGY_VAULT 환경변수 설정 (vault.py가 읽음)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kitlib"))
import vault  # noqa: E402

SOURCE_LABEL = {"claude": "Claude", "codex": "Codex", "chatgpt": "ChatGPT", "naver": "네이버"}
# 일일 롤업 대상: 세션이 실제로 그날 일어난 AI 어시스턴트/블로그 대화만.
# kakao/sms/notes는 방·계정별 아카이브라 date가 import 날짜에 몰려 일일 저널에 부적합 → 제외.
DAILY_SOURCES = set(SOURCE_LABEL)
_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def _day(note) -> str:
    m = _DATE.search(str(note.get("date", "")))
    return m.group(1) if m else ""


def _source(rel: str) -> str:
    parts = rel.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "conversations" else "기타"


def collect(days: int):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    by_day = defaultdict(lambda: defaultdict(list))  # day -> source -> [(title, project)]
    for f in vault._iter_notes("conversations"):
        n = vault._cached_note(f)
        if not n or n.get("_blocked"):
            continue
        day = _day(n)
        if not day or day < cutoff or day > today:
            continue
        src = _source(n["path"])
        if src not in DAILY_SOURCES:
            continue
        title = (n.get("title") or Path(n["path"]).stem)[:80]
        proj = n.get("project") or ""
        by_day[day][src].append((title, proj))
    return by_day


def _project_targets():
    """대화 project 필드값 → 실존 프로젝트 노트 stem. 위키링크가 깨지지 않게 해상용."""
    idx = {}
    for f in (vault.VAULT_PATH / "projects").rglob("*.md"):
        fm, _ = vault._parse_frontmatter(f.read_text(encoding="utf-8", errors="ignore"))
        for k in [f.stem, fm.get("name"), fm.get("project")] + (fm.get("aliases") or []):
            if k:
                idx[str(k).lower()] = f.stem
    return idx


def render(day: str, sources: dict, ptargets: dict) -> str:
    # 실존 프로젝트만 링크(없는 project 필드값은 깨진 위키링크가 되므로 제외)
    projects = sorted({ptargets[p.lower()] for items in sources.values()
                       for _, p in items if p and p.lower() in ptargets})
    total = sum(len(v) for v in sources.values())
    lines = [
        "---", "type: daily", f"date: {day}", "tags: [daily, auto]",
        "generated: true", f"conversation_count: {total}", "---", "",
        f"# {day}", "", "## 오늘 한 일", "",
        "## 대화 요약",
    ]
    for src in sorted(sources, key=lambda s: -len(sources[s])):
        label = SOURCE_LABEL.get(src, src)
        lines.append(f"- **{label}** ({len(sources[src])}건)")
        for title, _ in sources[src][:12]:
            lines.append(f"  - {title}")
    lines += ["", "## 관련 프로젝트",
              ("- " + ", ".join(f"[[{p}]]" for p in projects)) if projects else "- (없음)",
              "", "## 메모", "",
              "> 대화 노트에서 자동 집계된 일일 롤업(generated: true). 직접 메모를 추가하면 "
              "다음 갱신 때 보존하려면 generated 필드를 지우세요.", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()

    by_day = collect(args.days)
    daily_dir = vault.VAULT_PATH / "daily"
    daily_dir.mkdir(exist_ok=True)
    ptargets = _project_targets()
    written = skipped = 0
    for day in sorted(by_day, reverse=True):
        out = daily_dir / f"{day}.md"
        # 사용자가 손댄(generated 아님) 노트는 보존
        if out.exists():
            fm, _ = vault._parse_frontmatter(out.read_text(encoding="utf-8", errors="ignore"))
            if str(fm.get("generated", "")).lower() != "true":
                print(f"SKIP {day}: 사용자 노트 보존"); skipped += 1; continue
        total = sum(len(v) for v in by_day[day].values())
        print(f"{'APPLY' if args.apply else 'DRY'} {day}: {total}건 "
              f"({', '.join(f'{SOURCE_LABEL.get(s,s)}:{len(v)}' for s,v in by_day[day].items())})")
        if args.apply:
            out.write_text(render(day, by_day[day], ptargets), encoding="utf-8")
        written += 1
    print(f"\n{'적용' if args.apply else '미리보기'}: 롤업 {written}, 보존 {skipped}")


if __name__ == "__main__":
    main()
