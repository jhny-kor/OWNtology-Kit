#!/usr/bin/env python3
"""
classify_sensitivity.py — 내용 기반 민감도 상향 (위원회 3차 P0).

검색 summary로 개인정보가 새는 문제 중, '이름' 누출은 vault의 검색요약 마스킹이
처리한다. 여기서는 '주제 자체가 민감한 전용 노트'(가족여행 일정, 병원/진단, 금융
상세 등)를 title/summary 패턴으로 식별해 sensitivity 를 sensitive 로 올린다(기본
검색·조회 차단). 캐주얼 언급 오탐을 막기 위해 title 중심 고정밀 패턴만 사용한다.
private → sensitive 만 상향(public 창작물 등은 건드리지 않음). 되돌리기 로그 기록.

  python3 scripts/classify_sensitivity.py            # dry-run
  python3 scripts/classify_sensitivity.py --apply
"""
import os, re, sys, json, argparse
from pathlib import Path

import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1])); from kitlib.config import vault_path as _vp; VAULT = _vp()
SCAN_FOLDERS = ["conversations", "knowledge", "daily"]
SKIP = {".obsidian", ".omc", ".omx", "opencrab_data", "quarantine", "__pycache__",
        ".git", "_summaries", "_templates", "_merged"}
LOG = VAULT / "policies" / "sensitivity-elevations.json"

# title 고정밀 패턴 (전용 민감 노트). 한국어 다의어(진단=디버깅, 자산=리소스)는
# 제외하고, title 에 등장하면 거의 확실히 개인 민감 주제인 표현만 사용한다.
DEDICATED = re.compile(
    r"(?i)가족\s*여행|가족\s*일정|가족\s*모임|상견례|"
    r"연애|(?<!업)데이트|고백\s*편지|이별\s*편지|"
    r"병원\s*예약|진료\s*기록|건강검진\s*결과|처방전|입원|정신과|"
    r"계좌\s*번호|대출\s*(승인|상담|실행)|연봉\s*협상|급여\s*명세|카드\s*명세|재산\s*현황|"
    r"주민(등록)?\s*번호|여권\s*번호")


def _get(fm, key):
    m = re.search(rf"(?m)^{key}\s*:\s*(.*)$", fm)
    return m.group(1).strip() if m else None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    log = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
    done = {e["path"] for e in log}
    changed = 0
    for folder in SCAN_FOLDERS:
        base = VAULT / folder
        if not base.exists():
            continue
        for fp in base.rglob("*.md"):
            if any(p in SKIP for p in fp.parts):
                continue
            text = fp.read_text(encoding="utf-8", errors="ignore")
            if not text.startswith("---"):
                continue
            end = text.find("\n---", 3)
            fm = text[4:end]
            sens = (_get(fm, "sensitivity") or "private").strip()
            if sens != "private":
                continue  # sensitive/secret/public 은 건드리지 않음
            # title + 파일명만 검사 (summary/본문 캐주얼 언급은 오탐多 → 제외)
            hay = (_get(fm, "title") or "") + "\n" + fp.stem
            if not DEDICATED.search(hay):
                continue
            rel = str(fp.relative_to(VAULT))
            changed += 1
            if changed <= 10:
                print(f"  {'ELEVATE' if args.apply else 'DRY '} {rel}  private→sensitive")
            if args.apply:
                new_fm = re.sub(r"(?m)^sensitivity\s*:.*$", "sensitivity: sensitive", fm, count=1)
                fp.write_text(text[:4] + new_fm + text[end:], encoding="utf-8")
                if rel not in done:
                    log.append({"path": rel, "from": "private", "to": "sensitive"})
    if args.apply and changed:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[classify_sensitivity] {'APPLY' if args.apply else 'DRY-RUN'} — {changed} notes elevated")


if __name__ == "__main__":
    main()
