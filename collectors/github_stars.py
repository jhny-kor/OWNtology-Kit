#!/usr/bin/env python3
"""
GitHub Stars → owntology sync
config.json의 github.username 계정 공개 스타를 볼트에 동기화 (선택 소스)

사용법:
  python github_stars.py                        # 신규 스타만 노트 생성
  python github_stars.py --all                  # 전체 스타 재동기화
  python github_stars.py --summary              # 노트 생성 없이 현황만 출력
  python github_stars.py --enrich               # 기존 노트에 Claude 학습 요약 추가
  python github_stars.py --enrich --all         # 전체 재enrichment
  python github_stars.py --token <GITHUB_TOKEN> # GitHub Rate limit 방지
  python github_stars.py --api-key <KEY>        # Anthropic API 키 (--enrich 시 필요)
"""

import os
import json
import re
import sys
import time
import argparse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import load as _load_cfg, vault_path as _vp

_cfg = _load_cfg()
GITHUB_USER = (_cfg.get("github", {}).get("username") or "").strip()
VAULT_PATH = _vp()
REPOS_DIR = VAULT_PATH / "knowledge/github-stars/repos"
STATE_FILE = Path(__file__).parent / ".github_stars_state.json"

# --enrich 프롬프트에 들어가는 사용자 소개(프로젝트·관심사). 웹 설정 화면에서 입력.
USER_CONTEXT = (_cfg.get("github", {}).get("user_context") or "").strip()

CATEGORIES = [
    "AI/LLM/에이전트", "MCP 서버", "iOS/macOS", "보안",
    "백엔드/인프라", "웹/프론트엔드", "데이터/분석",
    "한국 특화", "개발 도구", "기타"
]


# ── Frontmatter 파서 ─────────────────────────────────────────

def _parse_fm(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_raw = text[4:end]
    body = text[end + 4:]
    fm = {}
    for line in fm_raw.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, body


def _build_fm(fm: dict) -> str:
    lines = ["---"]
    for k, v in fm.items():
        val = str(v)
        if val.startswith("[") or not val or val.isdigit():
            lines.append(f"{k}: {val}")
        elif " " in val or ":" in val or '"' in val:
            lines.append(f'{k}: "{val}"')
        else:
            lines.append(f"{k}: {val}")
    lines.append("---\n")
    return "\n".join(lines)


# ── GitHub API ───────────────────────────────────────────────

def slugify(text: str) -> str:
    return re.sub(r"[^\w가-힣-]", "-", text).strip("-")[:60]


def repo_note_filename(full_name: str) -> str:
    return f"{slugify(full_name.replace('/', '-'))}.md"


def fetch_stars(token: str = None) -> list[dict]:
    headers = {
        "Accept": "application/vnd.github.v3.star+json",
        "User-Agent": "owntology-sync/1.0",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/users/{GITHUB_USER}/starred?per_page=100&page={page}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            print(f"[오류] API 호출 실패 (page {page}): {e}")
            break
        if not data:
            break
        repos.extend(data)
        print(f"  page {page}: {len(data)}개 로드", end="\r")
        page += 1

    print(f"  총 {len(repos)}개 스타 로드됨          ")
    return repos


# ── 상태 관리 ────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"synced_ids": [], "last_sync": ""}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ── 노트 생성 ────────────────────────────────────────────────

def make_note(item: dict) -> tuple[str, str]:
    repo = item.get("repo") or item
    starred_at = item.get("starred_at", "")

    full_name = repo["full_name"]
    desc = (repo.get("description") or "").replace('"', "'")
    lang = repo.get("language") or ""
    stars = repo.get("stargazers_count", 0)
    topics = repo.get("topics", [])
    url = repo["html_url"]
    homepage = repo.get("homepage") or ""
    date = starred_at[:10] if starred_at else datetime.now().strftime("%Y-%m-%d")

    note_topics = ["github-repository"]
    if lang:
        note_topics.append(lang)
    note_topics += topics[:4]
    topics_yaml = "[" + ", ".join(f'"{t}"' for t in note_topics) + "]"

    filename = repo_note_filename(full_name)
    homepage_line = f"**Homepage:** {homepage}  \n" if homepage else ""
    body_topics = ", ".join(topics) if topics else "없음"

    summary = desc[:120] or f"GitHub repository: {full_name}"

    content = f"""---
title: "{full_name}"
type: note
date: {date}
summary: "{summary}"
topics: {topics_yaml}
source: github-stars
url: {url}
repo: {full_name}
starred_at: {starred_at}
language: {lang}
github_stars: {stars}
카테고리: 미분류
학습상태: 미확인
enriched: false
sensitivity: private
---

# {full_name}

{desc or "_설명 없음_"}

**URL:** {url}
{homepage_line}**Language:** {lang or "미기재"}
**Stars:** {stars:,}
**Topics:** {body_topics}
**Starred:** {date}

---

## 무엇인가
<!-- Claude 학습 요약 대기 중 — python github_stars_sync.py --enrich 실행 -->

## 핵심 학습 포인트
<!-- Claude 학습 요약 대기 중 -->

## 내 상황에서 활용 방법
<!-- Claude 학습 요약 대기 중 -->

## 내 메모
<!-- 직접 작성 -->
"""
    return filename, content


def make_summary_note(new_items: list[dict], date: str) -> tuple[str, str]:
    lines = []
    for item in new_items:
        repo = item.get("repo") or item
        full_name = repo["full_name"]
        desc = (repo.get("description") or "")[:80]
        lang = repo.get("language") or "미기재"
        stars = repo.get("stargazers_count", 0)
        url = repo["html_url"]
        lines.append(f"- **[{full_name}]({url})** `{lang}` ⭐{stars:,}  \n  {desc}")

    content = f"""---
title: "GitHub 신규 스타 - {date}"
type: note
date: {date}
summary: "{date} 기준 새로 추가된 GitHub Stars {len(new_items)}개"
topics: ["github-stars", "신규", "레퍼런스"]
source: github-stars
sensitivity: private
---

# GitHub 신규 스타 ({date})

총 **{len(new_items)}개** 신규 스타 추가됨.

{chr(10).join(lines)}

---
_개별 노트: `knowledge/github-stars/repos/` 폴더 참고_
"""
    return f"{date}-github-stars-new.md", content


# ── Claude Enrichment ────────────────────────────────────────

def _call_llm(prompt: str, client, provider: str) -> str:
    """provider에 따라 LLM 호출 후 텍스트 반환."""
    if provider == "openai":
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()
    else:  # anthropic
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()


def enrich_note(fpath: Path, client, provider: str) -> bool:
    """LLM으로 학습 요약 생성 후 노트 업데이트."""
    text = fpath.read_text(encoding="utf-8")
    fm, body = _parse_fm(text)

    if fm.get("enriched") == "true":
        return False  # 이미 처리됨

    repo_info = f"""레포: {fm.get('title', fpath.stem)}
설명: {fm.get('summary', '')}
언어: {fm.get('language', '')}
Stars: {fm.get('github_stars', 0)}
URL: {fm.get('url', '')}"""

    prompt = f"""다음 GitHub 레포지토리에 대해 학습/참고용 노트를 한국어로 작성해주세요.

{repo_info}

{USER_CONTEXT}

아래 형식으로 정확히 출력하세요. 각 섹션 제목은 그대로 유지하세요.

카테고리: [{", ".join(CATEGORIES)} 중 가장 적합한 하나]

## 무엇인가
(1~2문장. 이 레포가 어떤 문제를 해결하는지 핵심만)

## 핵심 학습 포인트
(번호 목록 2~4개. 실제로 배울 수 있는 기술/패턴/개념)

## 내 상황에서 활용 방법
(사용자 프로젝트와 연결한 구체적 아이디어 1~3개. 불가능하면 "직접 활용 어려움, 참고용" 한 줄)"""

    try:
        result = _call_llm(prompt, client, provider)
    except Exception as e:
        print(f"  [오류] {fpath.name}: {e}")
        return False

    # 카테고리 추출
    category = "기타"
    for line in result.splitlines():
        if line.startswith("카테고리:"):
            raw = line.replace("카테고리:", "").strip().strip("[]")
            for cat in CATEGORIES:
                if cat in raw:
                    category = cat
                    break
            else:
                category = raw
            break

    # 학습 섹션만 추출 (## 무엇인가 이후)
    study_start = result.find("## 무엇인가")
    study_section = result[study_start:].strip() if study_start != -1 else result

    # 기존 body에서 --- 이후 섹션 제거, 내 메모 보존
    user_memo = ""
    if "## 내 메모" in body:
        memo_start = body.index("## 내 메모")
        memo_text = body[memo_start + len("## 내 메모"):].strip()
        if memo_text and "<!-- 직접 작성 -->" not in memo_text:
            user_memo = memo_text

    if "---" in body:
        body = body[:body.index("---")].rstrip()

    fm["카테고리"] = category
    fm["학습상태"] = "미확인"
    fm["enriched"] = "true"

    memo_content = user_memo if user_memo else "<!-- 직접 작성 -->"
    new_text = (
        _build_fm(fm)
        + body.rstrip()
        + f"\n\n---\n\n{study_section}\n\n## 내 메모\n{memo_content}\n"
    )
    fpath.write_text(new_text, encoding="utf-8")
    return True


def run_enrich(api_key: str, provider: str, force_all: bool = False):
    """REPOS_DIR의 미enriched 노트에 LLM 학습 요약 추가."""
    if provider == "openai":
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
        except ImportError:
            print("[오류] openai 패키지 없음. .venv/bin/python 사용 필요")
            sys.exit(1)
    else:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            print("[오류] anthropic 패키지 없음. .venv/bin/python 사용 필요")
            sys.exit(1)

    model_name = "gpt-4o-mini" if provider == "openai" else "claude-haiku"
    print(f"[enrichment] 모델: {model_name}")

    notes = sorted(REPOS_DIR.glob("*.md"))
    targets = []
    for fpath in notes:
        text = fpath.read_text(encoding="utf-8")
        fm, _ = _parse_fm(text)
        if force_all or fm.get("enriched") != "true":
            targets.append(fpath)

    if not targets:
        print("[완료] 모든 노트가 이미 enriched 상태입니다.")
        return

    print(f"[enrichment] {len(targets)}개 노트 처리 시작 ({model_name})")
    done = 0
    for i, fpath in enumerate(targets, 1):
        print(f"  [{i}/{len(targets)}] {fpath.name[:50]}...", end=" ")
        ok = enrich_note(fpath, client, provider)
        if ok:
            print("✓")
            done += 1
        else:
            print("스킵")
        if i % 10 == 0:
            time.sleep(1)  # Rate limit 방지

    print(f"\n[완료] {done}개 enriched → {REPOS_DIR}")


# ── 메인 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GitHub Stars → owntology sync")
    parser.add_argument("--all", action="store_true", help="전체 재동기화/재enrichment")
    parser.add_argument("--summary", action="store_true", help="현황만 출력 (파일 생성 없음)")
    parser.add_argument("--enrich", action="store_true", help="LLM으로 학습 요약 생성")
    parser.add_argument("--token", default=None, help="GitHub personal access token")
    parser.add_argument("--openai-key", default=None, dest="openai_key", help="OpenAI API 키 (gpt-4o-mini)")
    parser.add_argument("--api-key", default=None, dest="api_key", help="Anthropic API 키 (claude-haiku)")
    args = parser.parse_args()

    if not GITHUB_USER:
        print("[github-stars] config.json github.username 미설정 — 건너뜀 "
              "(웹 설정 화면 또는 config.json에서 입력)")
        return

    # Enrich 모드
    if args.enrich:
        openai_key = args.openai_key or os.getenv("OPENAI_API_KEY", "")
        anthropic_key = args.api_key or os.getenv("ANTHROPIC_API_KEY", "")
        if openai_key:
            run_enrich(api_key=openai_key, provider="openai", force_all=args.all)
        elif anthropic_key:
            run_enrich(api_key=anthropic_key, provider="anthropic", force_all=args.all)
        else:
            print("[오류] --openai-key <KEY> 또는 --api-key <KEY> 필요")
            sys.exit(1)
        return

    # Sync 모드
    print(f"[owntology] GitHub Stars 동기화 시작 ({GITHUB_USER})")
    items = fetch_stars(token=args.token)
    if not items:
        print("[오류] 스타 목록을 가져오지 못했습니다.")
        sys.exit(1)

    state = load_state()
    synced_ids = set(state.get("synced_ids", []))
    today = datetime.now().strftime("%Y-%m-%d")

    new_items, all_ids = [], []
    for item in items:
        repo = item.get("repo") or item
        repo_id = repo["id"]
        all_ids.append(repo_id)
        if args.all or repo_id not in synced_ids:
            new_items.append(item)

    print(f"  전체: {len(items)}개 | 신규: {len(new_items)}개 | 기존: {len(items)-len(new_items)}개")

    if args.summary:
        print("\n[신규 스타 목록]")
        for item in new_items:
            repo = item.get("repo") or item
            print(f"  ⭐ {repo['full_name']} ({repo.get('language','')}) — {(repo.get('description') or '')[:60]}")
        return

    if not new_items:
        print("[완료] 새로운 스타 없음.")
        return

    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    created, skipped = 0, 0
    for item in new_items:
        filename, content = make_note(item)
        fpath = REPOS_DIR / filename
        if fpath.exists() and not args.all:
            skipped += 1
            continue
        fpath.write_text(content, encoding="utf-8")
        created += 1

    print(f"  개별 노트: {created}개 생성 / {skipped}개 스킵")

    if created > 0:
        print("  요약 노트: repo 단위 관리 모드라 생성하지 않음")

    state["synced_ids"] = list(set(synced_ids) | set(all_ids))
    state["last_sync"] = today
    save_state(state)
    print(f"\n[완료] {created}개 노트 생성")
    if created > 0:
        print(f"  학습 요약 추가: .venv/bin/python github_stars_sync.py --enrich --api-key <KEY>")


if __name__ == "__main__":
    main()
