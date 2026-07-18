#!/usr/bin/env python3
"""
scan_secrets.py — 볼트 전수 비밀정보 스캔 (위원회 P0-5)

볼트의 .md 노트를 파일명/본문 기준으로 검사해 비밀번호·인증링크·OTP·API키·
org 초대·금융·사설망 등 민감 의심 문서를 분류한다. 파일을 수정하지 않고
리포트(policies/secret-scan-report.json)만 출력한다. quarantine.py 입력으로 사용.

사용법:
  python3 scripts/scan_secrets.py                # 전체 스캔 → 리포트 출력
  python3 scripts/scan_secrets.py --folder source/email
"""

import re, json, argparse

from pathlib import Path
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()

SKIP_DIRS = {".obsidian", ".omc", ".omx", ".git", "opencrab_data",
             "quarantine", "__pycache__", "_summaries", "node_modules"}

# (카테고리, 정규식). **값 기반** 탐지 — 실제 비밀 값/링크/코드가 있어야 매칭한다.
# 주제어 언급("API key 연동", "초대")만으로는 플래그하지 않는다(오탐 방지).
# 반환 텍스트는 security.redact 가 별도로 마스킹하므로, 격리는 고신뢰 문서에만.
PATTERNS = {
    # 실제 키 material 또는 'key/secret/token/비밀번호 = <값>' 형태만
    "api_key": re.compile(
        r"\bsk-[A-Za-z0-9]{20,}\b"
        r"|\bsk-proj-[A-Za-z0-9_-]{20,}\b"
        r"|\bgh[pousr]_[A-Za-z0-9]{30,}\b"
        r"|\bxox[baprs]-[A-Za-z0-9-]{20,}\b"
        r"|\bAKIA[0-9A-Z]{16}\b"
        r"|\b(?:api[_\- ]?key|access[_\- ]?key|secret[_\- ]?key|client[_\- ]?secret|"
        r"인증키|비밀번호|password|passwd|pwd|token)\b\s*[:=]\s*[\"']?[A-Za-z0-9/+._-]{12,}",
        re.IGNORECASE),
    # 토큰/코드/매직 파라미터가 포함된 실제 인증 링크만
    "auth_link": re.compile(
        r"(?i)https?://[^\s)>\]]*"
        r"(?:reset[_-]?password|verify[_-]?email|magic[_-]?link|confirm[_-]?email|"
        r"[?&](?:token|code|access_token|otp|invite|invitation)=)[^\s)>\]]+"),
    # 인증/로그인 코드 + 실제 4~8자리 숫자
    "otp_code": re.compile(
        r"(?i)(임시\s*\S*\s*로그인\s*코드|인증\s*(번호|코드)|verification\s*code|"
        r"one[\- ]?time\s*(code|password)|로그인\s*코드)\D{0,12}\b\d{4,8}\b"),
    # 사설망 IP / .local 호스트 (정보성 — 기본 격리 대상 아님)
    "private_net": re.compile(
        r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b192\.168\.\d{1,3}\.\d{1,3}\b|"
        r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"),
}


def iter_md(base: Path):
    for fp in base.rglob("*.md"):
        if any(part in SKIP_DIRS for part in fp.parts):
            continue
        yield fp


def scan_file(fp: Path) -> list[str]:
    try:
        text = fp.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    haystack = fp.name + "\n" + text
    return [cat for cat, pat in PATTERNS.items() if pat.search(haystack)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=None, help="특정 폴더만 스캔 (볼트 상대경로)")
    ap.add_argument("--out", default="policies/secret-scan-report.json")
    args = ap.parse_args()

    base = VAULT / args.folder if args.folder else VAULT
    findings = []
    cat_counts: dict = {}
    for fp in iter_md(base):
        cats = scan_file(fp)
        if cats:
            rel = str(fp.relative_to(VAULT))
            findings.append({"path": rel, "categories": cats})
            for c in cats:
                cat_counts[c] = cat_counts.get(c, 0) + 1

    report = {
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "vault": str(VAULT),
        "scope": args.folder or "(whole vault)",
        "total_flagged": len(findings),
        "category_counts": dict(sorted(cat_counts.items(), key=lambda x: -x[1])),
        "findings": sorted(findings, key=lambda x: x["path"]),
    }
    out = VAULT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[scan_secrets] 스캔 범위: {report['scope']}")
    print(f"[scan_secrets] 플래그된 문서: {report['total_flagged']}건")
    for c, n in report["category_counts"].items():
        print(f"  - {c}: {n}")
    print(f"[scan_secrets] 리포트: {out}")


if __name__ == "__main__":
    main()
