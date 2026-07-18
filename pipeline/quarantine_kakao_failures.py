#!/usr/bin/env python3
"""
quarantine_kakao_failures.py — 깨진 카카오 export 파일 격리 (위원회 재평가 P0).

kmsg(UI 자동화) export 가 채팅창을 못 찾으면 에러 텍스트를 .json 으로 써서
파싱 실패 부채가 쌓인다. 이 스크립트가 source/kakao/kmsg-*.json 중
  - 0바이트(빈 파일)
  - '{' 로 시작하지 않음(에러 텍스트)
  - JSON 파싱 실패 / 객체 아님
을 quarantine/kakao-export-failures/ 로 격리한다(삭제 아님, 로그). 한 번 격리되면
source/kakao 에서 빠지므로 재처리되지 않는다. one_touch_sync 에서 자동 호출 가능.

  python3 scripts/quarantine_kakao_failures.py            # dry-run
  python3 scripts/quarantine_kakao_failures.py --apply
"""
import sys, json, shutil, argparse

from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()
SRC = VAULT / "source" / "kakao"
QDIR = VAULT / "quarantine" / "kakao-export-failures"
LOG = VAULT / "policies" / "kakao-export-failures-log.json"


def classify(fp: Path):
    """격리 사유 반환(정상이면 None)."""
    try:
        size = fp.stat().st_size
    except OSError:
        return "stat-error"
    if size == 0:
        return "empty"
    try:
        text = fp.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return "read-error"
    if not text.startswith("{"):
        return "not-json (error-text)"
    try:
        data = json.loads(text)
    except Exception as e:
        return f"json-decode ({type(e).__name__})"
    if not isinstance(data, dict):
        return "not-an-object"
    return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not SRC.exists():
        print("source/kakao 없음"); return
    log = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
    moved = 0
    by_reason = {}
    for fp in sorted(SRC.glob("kmsg-*.json")):
        reason = classify(fp)
        if reason is None:
            continue
        by_reason[reason.split(" ")[0]] = by_reason.get(reason.split(" ")[0], 0) + 1
        moved += 1
        if moved <= 8:
            print(f"  {'MOVE' if args.apply else 'DRY '} {fp.name}  [{reason}]")
        if args.apply:
            QDIR.mkdir(parents=True, exist_ok=True)
            dest = QDIR / fp.name
            i = 1
            while dest.exists():
                dest = QDIR / f"{fp.stem}__{i}{fp.suffix}"; i += 1
            shutil.move(str(fp), str(dest))
            log.append({"moved_at": datetime.now().isoformat(timespec="seconds"),
                        "from": str(fp.relative_to(VAULT)), "to": str(dest.relative_to(VAULT)),
                        "reason": reason})
    if args.apply and moved:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[quarantine_kakao_failures] {'APPLY' if args.apply else 'DRY-RUN'} — "
          f"{moved} bad files ({by_reason})")


if __name__ == "__main__":
    main()
