#!/usr/bin/env python3
"""owntology-kit — 개인 데이터(카카오톡·SMS·메일·메모·Safari 탭·GitHub 스타)를
수집해 온톨로지 볼트로 만드는 원터치 CLI.

    python3 kit.py init         # config.json 생성 + 볼트 스캐폴드
    python3 kit.py collect      # 활성 소스 수집 (source/ 원문)
    python3 kit.py ontologize   # 원문 → 대화노트 → 엔티티/관계/인덱스/검증
    python3 kit.py run          # collect + ontologize 원터치
    python3 kit.py web          # 설정·수동입력 웹 화면 (127.0.0.1:8765)

수집 단계는 권한/앱 문제 시 경고만 남기고 계속 진행한다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))
from kitlib import config as kitconfig

COLLECTORS = KIT / "collectors"
PIPELINE = KIT / "pipeline"

VAULT_DIRS = [
    "source/kakao", "source/sms", "source/email", "source/apple-notes",
    "source/safari-tabs/raw",
    "conversations/kakao", "conversations/sms", "conversations/notes",
    "people", "organizations", "projects",
    "knowledge/links/nodes", "knowledge/github-stars/repos",
    "decisions", "events", "preferences", "daily",
    "indexes", "ontology", "policies", "quarantine", "archive", "schemas",
]

VAULT_README = """# 내 온톨로지 볼트

owntology-kit이 관리하는 개인 지식 볼트입니다.

| 폴더 | 용도 |
|------|------|
| `source/` | 수집 원문 (카카오·SMS·메일·메모·Safari 탭) — 검색 기본 제외 |
| `conversations/` | 원문에서 만든 대화/노트 (라이브 정본) |
| `people/` `organizations/` `projects/` | 엔티티 노트 (entity_id·relations) |
| `knowledge/` | 링크 노드·GitHub 스타 |
| `decisions/` `events/` `preferences/` `daily/` | 사실 노트 (personal-layer 승인 시 생성) |
| `indexes/` `ontology/` `schemas/` | 인덱스·카탈로그·스키마 (인프라) |
| `quarantine/` | 파싱 실패/민감 격리 — 검색 제외 |

갱신: `python3 kit.py run` / 수동입력: `python3 kit.py web`
"""


def _step(name: str, cmd: list[str], optional: bool = True) -> bool:
    """한 단계 실행. optional이면 실패 시 경고만 남기고 계속(권한/미설치 대비)."""
    print(f"▶ {name}")
    proc = subprocess.run(cmd, cwd=str(KIT), text=True, capture_output=True)
    out = (proc.stdout + proc.stderr).strip()
    tail = out.splitlines()[-1] if out else ""
    if proc.returncode != 0:
        msg = out[-800:] if out else f"exit {proc.returncode}"
        if optional:
            print(f"  ⚠️ WARN skipped: {msg[:300]}")
            return False
        print(f"  ❌ FAIL: {msg}", file=sys.stderr)
        raise SystemExit(1)
    print(f"  {tail[:200]}" if tail else "  ok")
    return True


def cmd_init(args) -> int:
    cfg = kitconfig.load()
    if not kitconfig.CONFIG_FILE.exists():
        kitconfig.save(cfg)
        print(f"config 생성: {kitconfig.CONFIG_FILE}")
    else:
        print(f"config 존재: {kitconfig.CONFIG_FILE} (유지)")

    vault = kitconfig.vault_path()
    for d in VAULT_DIRS:
        (vault / d).mkdir(parents=True, exist_ok=True)
    for schema in (KIT / "schemas").glob("*.json"):
        dst = vault / "schemas" / schema.name
        if not dst.exists():
            shutil.copy2(schema, dst)
    readme = vault / "README.md"
    if not readme.exists():
        readme.write_text(VAULT_README, encoding="utf-8")
    print(f"볼트 스캐폴드 완료: {vault}")
    print("다음 단계: python3 kit.py web 으로 설정 입력 → python3 kit.py run")
    return 0


def cmd_collect(args) -> int:
    cfg = kitconfig.load()
    os.environ["OWNTOLOGY_VAULT"] = str(kitconfig.vault_path())
    src = cfg.get("sources", {})
    py = sys.executable

    if src.get("kakao"):
        kakao_cmd = [py, str(COLLECTORS / "kakao_export.py"), "--no-sync"]
        if getattr(args, "fast_kakao", False):
            kakao_cmd.append("--no-katok-sync")
        _step("collect/kakao", kakao_cmd)
    if src.get("sms"):
        _step("collect/sms", [py, str(COLLECTORS / "sms_export.py"),
                              "--limit", str(cfg.get("sms", {}).get("limit", 500))])
    if src.get("mail"):
        _step("collect/mail", [py, str(COLLECTORS / "mail_export.py"),
                               "--limit", str(cfg.get("mail", {}).get("limit", 300)),
                               "--days", str(cfg.get("mail", {}).get("days", 14))])
    if src.get("notes"):
        _step("collect/notes", [py, str(COLLECTORS / "notes_export.py")])
    if src.get("safari_tabs"):
        _step("collect/safari-tabs", [py, str(COLLECTORS / "safari_tabs_export.py")])
    if src.get("github_stars"):
        _step("collect/github-stars", [py, str(COLLECTORS / "github_stars.py")])
        if os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY"):
            _step("enrich/github-stars", [py, str(COLLECTORS / "github_stars.py"), "--enrich"])
    return 0


def cmd_ontologize(args) -> int:
    os.environ["OWNTOLOGY_VAULT"] = str(kitconfig.vault_path())
    py = sys.executable
    stages = kitconfig.load().get("pipeline", {})

    def p(script: str, *extra: str) -> list[str]:
        return [py, str(PIPELINE / script), *extra]

    # 1) 원문 → 대화 노트 + 실패 격리 (+ 인물 스텁 — 선택)
    _step("sync/notes", p("sync_notes.py"), optional=False)
    _step("quarantine/kakao-failures", p("quarantine_kakao_failures.py", "--apply"))
    if stages.get("member_stubs", True):
        _step("people/member-stubs", p("create_member_stubs.py", "--apply"))

    # 2) 정제·구조화 (enhance 체인, 전부 idempotent — 핵심 단계, 항상 실행)
    for script in ("normalize_entities.py", "assign_entity_ids.py",
                   "fix_double_frontmatter.py", "apply_temporal.py",
                   "apply_fact_status.py", "classify_sensitivity.py",
                   "migrate_relations.py", "link_project_orgs.py"):
        _step(f"enhance/{script[:-3]}", p(script, "--apply"))
    _step("enhance/scan_secrets", p("scan_secrets.py"))  # 리포트만

    # 3) 링크 노드 (카카오 링크 enrich → kakao+safari 통합 노드 — 선택)
    if stages.get("link_nodes", True):
        _step("links/enrich-kakao", p("enrich_kakao_links.py", "--all"))
        _step("links/nodes", p("build_link_nodes.py"))

    # 4) 인덱스 재빌드 (핵심 단계, 항상 실행)
    for script in ("build_relations_index.py", "build_conversation_links.py",
                   "build_topic_taxonomy.py"):
        _step(f"index/{script[:-3]}", p(script, "--build"))

    # 5) 개인계층 후보(전부 proposed — 승인 시에만 노트 승격) + 일일 롤업 (선택)
    if stages.get("personal_layer", True):
        _step("personal-layer", p("extract_personal_layer.py", "--apply", "--kakao-days", "90"))
    if stages.get("daily_rollup", True):
        _step("daily/rollup", p("daily_rollup.py", "--apply", "--days", "14"))

    # 6) 검증 (위반 시 경고, 중단 안 함)
    _step("validate/ontology", p("validate_ontology.py"))
    _step("validate/relations", p("validate_relations.py"))
    return 0


def cmd_run(args) -> int:
    cmd_collect(args)
    cmd_ontologize(args)
    print("\n원터치 완료. 수동입력 필드는 python3 kit.py web 에서 채우세요.")
    return 0


def cmd_web(args) -> int:
    os.environ["OWNTOLOGY_VAULT"] = str(kitconfig.vault_path())
    return subprocess.call([sys.executable, str(KIT / "web" / "server.py")])


def main() -> int:
    ap = argparse.ArgumentParser(description="owntology-kit — 수집·온톨로지화 원터치")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="config 생성 + 볼트 스캐폴드")
    c = sub.add_parser("collect", help="활성 소스 수집")
    c.add_argument("--fast-kakao", action="store_true",
                   help="katok sync(DB 복호화, 수 분) 생략, 기존 아카이브에서 export")
    sub.add_parser("ontologize", help="원문 → 온톨로지화")
    r = sub.add_parser("run", help="collect + ontologize")
    r.add_argument("--fast-kakao", action="store_true")
    sub.add_parser("web", help="설정·수동입력 웹 화면")
    args = ap.parse_args()
    return {"init": cmd_init, "collect": cmd_collect, "ontologize": cmd_ontologize,
            "run": cmd_run, "web": cmd_web}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
