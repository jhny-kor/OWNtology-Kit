#!/usr/bin/env python3
"""owntology-kit — 개인 데이터(카카오톡·SMS·메일·메모·Safari 탭·GitHub 스타)를
수집해 온톨로지 볼트로 만드는 원터치 CLI.

    python3 kit.py init         # config.json 생성 + 볼트 스캐폴드
    python3 kit.py doctor       # 환경·설정·권한 사전 점검
    python3 kit.py collect      # 활성 소스 수집 (source/ 원문)
    python3 kit.py ontologize   # 원문 → 대화노트 → 엔티티/관계/인덱스/검증
    python3 kit.py run          # collect + ontologize 원터치
    python3 kit.py web          # 설정·수동입력 웹 화면 (127.0.0.1:8765)

기본 수집 소스는 전부 꺼져 있다 — kit.py web 에서 켜야 수집한다.

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
    print("\n⚠️ 개인정보 안내: 이 볼트에는 카카오톡·문자·메일·메모 등 원문이 "
          "평문 마크다운으로 저장되며, 대화 상대 등 제3자의 개인정보가 포함될 수 있습니다.\n"
          "  - 볼트를 공개 저장소·공유 폴더에 두지 마세요(iCloud/Dropbox 동기화 주의).\n"
          "  - 자세한 내용은 PRIVACY.md 참고.")
    print("\n다음 단계: python3 kit.py web 으로 설정 입력(수집 소스 켜기) → python3 kit.py run")
    return 0


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def cmd_doctor(args) -> int:
    """실행 전 환경·설정 사전 점검. 각 항목 OK/WARN/FAIL 표시."""
    import platform
    cfg = kitconfig.load()
    src = cfg.get("sources", {})
    rows: list[tuple[str, str]] = []

    def add(status: str, msg: str):
        rows.append((status, msg))

    add("OK", f"macOS {platform.mac_ver()[0]}" if sys.platform == "darwin"
        else f"⚠️ 이 킷은 macOS 전용 (현재: {sys.platform})")
    pyv = sys.version_info
    add("OK" if pyv >= (3, 11) else "FAIL",
        f"Python {pyv.major}.{pyv.minor}" + ("" if pyv >= (3, 11) else " (3.11+ 필요)"))

    vault = kitconfig.vault_path()
    add("OK" if vault.exists() else "WARN",
        f"볼트: {vault}" + ("" if vault.exists() else " (없음 — kit.py init 먼저)"))
    if any(s in str(vault) for s in ("Mobile Documents", "Dropbox", "Google Drive", "OneDrive")):
        add("WARN", "볼트가 클라우드 동기화 경로에 있습니다 — 평문 개인정보 유출 주의")

    if src.get("kakao"):
        add("OK" if _which("katok") else "FAIL", "katok CLI" + ("" if _which("katok")
            else " 미설치 — github.com/NomaDamas/katok"))
        add("OK" if (cfg.get("me", {}).get("kakao_nickname") or "").strip() else "WARN",
            "카카오 닉네임" + ("" if (cfg.get("me", {}).get("kakao_nickname") or "").strip()
            else " 미설정 — 카카오 수집이 건너뜁니다(kit.py web)"))
    if src.get("sms"):
        add("OK" if _which("imsg") else "FAIL", "imsg CLI" + ("" if _which("imsg")
            else " 미설치 — github.com/openclaw/imsg"))
        msgdb = Path("~/Library/Messages/chat.db").expanduser()
        add("OK" if os.access(msgdb, os.R_OK) else "FAIL",
            "Messages DB 접근" + ("" if os.access(msgdb, os.R_OK)
            else " 불가 — 터미널에 Full Disk Access 부여"))
    if src.get("safari_tabs"):
        db = Path("~/Library/Containers/com.apple.Safari/Data/Library/Safari/CloudTabs.db").expanduser()
        add("OK" if os.access(db, os.R_OK) else "FAIL",
            "Safari CloudTabs.db" + ("" if os.access(db, os.R_OK)
            else " 접근 불가 — iCloud 탭 동기화 + Full Disk Access 확인"))
    if src.get("mail"):
        add("WARN", "Mail.app 자동화 권한은 첫 collect 실행 시 팝업으로 허용해야 합니다")
    if src.get("notes"):
        add("WARN", "Notes.app 자동화 권한은 첫 collect 실행 시 팝업으로 허용해야 합니다")
    if src.get("github_stars"):
        add("OK" if (cfg.get("github", {}).get("username") or "").strip() else "FAIL",
            "GitHub 사용자명" + ("" if (cfg.get("github", {}).get("username") or "").strip()
            else " 미설정(kit.py web)"))
    if not any(src.values()):
        add("WARN", "수집 소스가 모두 꺼져 있음 — kit.py web 에서 켜세요")

    icon = {"OK": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]"}
    print("owntology-kit doctor")
    for status, msg in rows:
        print(f"{icon.get(status, status)} {msg}")
    n_fail = sum(1 for s, _ in rows if s == "FAIL")
    print(f"\n요약: FAIL {n_fail} / WARN {sum(1 for s, _ in rows if s == 'WARN')}")
    return 1 if n_fail else 0


CORE_SOURCES = ("kakao", "sms", "mail", "notes", "safari_tabs")
# 수집기가 '미설치/권한없음'을 알리는 종료코드 — 실패가 아니라 '건너뜀'으로 분류.
_SKIP_EXIT_CODES = {2, 3}


def _run_collector(name: str, cmd: list[str]) -> dict:
    """수집기 1개 실행 → {name, status(ok|skip|fail), detail}. 예외를 삼키지 않고 분류."""
    print(f"▶ {name}")
    proc = subprocess.run(cmd, cwd=str(KIT), text=True, capture_output=True)
    out = (proc.stdout + proc.stderr).strip()
    tail = out.splitlines()[-1][:200] if out else ""
    if proc.returncode == 0:
        print(f"  ✓ {tail}" if tail else "  ✓ ok")
        return {"name": name, "status": "ok", "detail": tail}
    status = "skip" if proc.returncode in _SKIP_EXIT_CODES else "fail"
    icon = "⏭️ SKIP" if status == "skip" else "❌ FAIL"
    print(f"  {icon}: {tail or ('exit ' + str(proc.returncode))}")
    return {"name": name, "status": status, "detail": tail or f"exit {proc.returncode}"}


def _collect(cfg: dict, args) -> list[dict]:
    os.environ["OWNTOLOGY_VAULT"] = str(kitconfig.vault_path())
    src = cfg.get("sources", {})
    py = sys.executable
    results: list[dict] = []

    if src.get("kakao"):
        # 카카오 수집은 본인 닉네임이 필수(없으면 본인 메시지가 "나"로 매핑 안 됨) — 하드 게이트.
        if not (cfg.get("me", {}).get("kakao_nickname") or "").strip():
            print("▶ collect/kakao\n  ⏭️ SKIP: 카카오 닉네임 미설정 — kit.py web 에서 입력 후 재실행")
            results.append({"name": "collect/kakao", "status": "skip",
                            "detail": "카카오 닉네임(me.kakao_nickname) 미설정"})
        else:
            kakao_cmd = [py, str(COLLECTORS / "kakao_export.py"), "--no-sync"]
            if getattr(args, "fast_kakao", False):
                kakao_cmd.append("--no-katok-sync")
            results.append(_run_collector("collect/kakao", kakao_cmd))
    if src.get("sms"):
        results.append(_run_collector("collect/sms", [py, str(COLLECTORS / "sms_export.py"),
                       "--limit", str(cfg.get("sms", {}).get("limit", 500))]))
    if src.get("mail"):
        results.append(_run_collector("collect/mail", [py, str(COLLECTORS / "mail_export.py"),
                       "--limit", str(cfg.get("mail", {}).get("limit", 300)),
                       "--days", str(cfg.get("mail", {}).get("days", 14))]))
    if src.get("notes"):
        results.append(_run_collector("collect/notes", [py, str(COLLECTORS / "notes_export.py")]))
    if src.get("safari_tabs"):
        results.append(_run_collector("collect/safari-tabs",
                       [py, str(COLLECTORS / "safari_tabs_export.py")]))
    if src.get("github_stars"):
        results.append(_run_collector("collect/github-stars",
                       [py, str(COLLECTORS / "github_stars.py")]))
        if os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY"):
            results.append(_run_collector("enrich/github-stars",
                           [py, str(COLLECTORS / "github_stars.py"), "--enrich"]))
    if not results:
        print("수집 소스가 모두 꺼져 있습니다 — kit.py web 에서 최소 한 개를 켜세요.")
    return results


def _collect_exit_code(results: list[dict]) -> int:
    """수집 요약 출력 + 종료코드 결정. 시도한 핵심 수집기가 모두 실패하면 non-zero."""
    if not results:
        return 0
    icon = {"ok": "성공", "skip": "건너뜀", "fail": "실패"}
    print("\n── 수집 결과 ──")
    for r in results:
        print(f"- {r['name'].replace('collect/', '')}: {icon[r['status']]}"
              + (f" — {r['detail']}" if r["detail"] else ""))
    n_ok = sum(1 for r in results if r["status"] == "ok")
    n_skip = sum(1 for r in results if r["status"] == "skip")
    n_fail = sum(1 for r in results if r["status"] == "fail")
    print(f"전체 성공: {n_ok} / 실패: {n_fail} / 건너뜀: {n_skip}")

    core = [r for r in results if r["name"].replace("collect/", "").replace("-", "_")
            in CORE_SOURCES]
    core_attempted = [r for r in core if r["status"] != "skip"]
    if core_attempted and not any(r["status"] == "ok" for r in core_attempted):
        print("⚠️ 시도한 핵심 수집기가 모두 실패했습니다.", file=sys.stderr)
        return 1
    return 0


def cmd_collect(args) -> int:
    return _collect_exit_code(_collect(kitconfig.load(), args))


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
    results = _collect(kitconfig.load(), args)
    collect_code = _collect_exit_code(results)
    cmd_ontologize(args)
    if collect_code:
        print("\n⚠️ 일부/전체 수집 실패 — 위 '수집 결과'를 확인하세요. "
              "수동입력 필드는 python3 kit.py web 에서 채웁니다.", file=sys.stderr)
    else:
        print("\n수집·온톨로지화 처리 완료. 수동입력 필드는 python3 kit.py web 에서 채웁니다.")
    return collect_code


def cmd_web(args) -> int:
    os.environ["OWNTOLOGY_VAULT"] = str(kitconfig.vault_path())
    return subprocess.call([sys.executable, str(KIT / "web" / "server.py")])


def main() -> int:
    ap = argparse.ArgumentParser(description="owntology-kit — 수집·온톨로지화 원터치")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="config 생성 + 볼트 스캐폴드")
    sub.add_parser("doctor", help="환경·설정·권한 사전 점검")
    c = sub.add_parser("collect", help="활성 소스 수집")
    c.add_argument("--fast-kakao", action="store_true",
                   help="katok sync(DB 복호화, 수 분) 생략, 기존 아카이브에서 export")
    sub.add_parser("ontologize", help="원문 → 온톨로지화")
    r = sub.add_parser("run", help="collect + ontologize")
    r.add_argument("--fast-kakao", action="store_true")
    sub.add_parser("web", help="설정·수동입력 웹 화면")
    args = ap.parse_args()
    return {"init": cmd_init, "doctor": cmd_doctor, "collect": cmd_collect,
            "ontologize": cmd_ontologize, "run": cmd_run, "web": cmd_web}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
