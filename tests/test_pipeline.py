#!/usr/bin/env python3
"""owntology-kit 스모크 테스트 — 샘플 카카오/SMS 원문으로 파이프라인 검증.

외부 앱·CLI 없이(수집기 제외) sync_notes → 검증까지 돈다. assert 기반, 프레임워크 불필요.

    python3 tests/test_pipeline.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]


def _seed(vault: Path) -> None:
    (vault / "source" / "kakao").mkdir(parents=True, exist_ok=True)
    (vault / "source" / "sms").mkdir(parents=True, exist_ok=True)
    (vault / "conversations").mkdir(parents=True, exist_ok=True)
    kakao = {"chat": "샘플방", "count": 2, "messages": [
        {"author": "나", "body": "다음 주에 배포하기로 했어", "time_raw_with_date": "2026-07-01 오후 3:10"},
        {"author": "홍길동", "body": "네 좋아요", "time_raw_with_date": "2026-07-01 오후 3:12"},
    ]}
    (vault / "source" / "kakao" / "kmsg-katok-샘플방.json").write_text(
        json.dumps(kakao, ensure_ascii=False), encoding="utf-8")
    sms = {"meta": {"chat_id": 1, "identifier": "01000000000", "service": "SMS",
                    "exported_at": "2026-07-10T00:00:00+00:00"},
           "messages": [{"date": "2026-07-09T12:00:00Z", "sender": "01000000000",
                         "is_from_me": False, "text": "샘플 문자"}]}
    (vault / "source" / "sms" / "sms-SMS-01000000000-1.json").write_text(
        json.dumps(sms, ensure_ascii=False), encoding="utf-8")


def _run(script: str, *args: str, vault: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "OWNTOLOGY_VAULT": str(vault)}
    return subprocess.run([sys.executable, str(KIT / script), *args],
                          cwd=str(KIT), env=env, text=True, capture_output=True)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        # init 스캐폴드 (config는 임시 vault를 가리키도록 env로 강제)
        r = _run("kit.py", "init", vault=vault)
        assert r.returncode == 0, f"init 실패: {r.stderr}"
        _seed(vault)

        # 원문 → 대화 노트
        r = _run("pipeline/sync_notes.py", vault=vault)
        assert r.returncode == 0, f"sync_notes 실패: {r.stderr}"
        kakao_note = vault / "conversations" / "kakao" / "샘플방.md"
        assert kakao_note.exists(), "카카오 대화 노트 미생성"
        body = kakao_note.read_text(encoding="utf-8")
        assert "배포하기로" in body and "나" in body, "카카오 본문 누락"
        sms_notes = list((vault / "conversations" / "sms").glob("*.md"))
        assert sms_notes, "SMS 대화 노트 미생성"

        # 인물 스텁(1:1 아님 → 생성 안 됨) + 검증만 확인
        r = _run("pipeline/validate_ontology.py", vault=vault)
        assert r.returncode == 0, f"validate_ontology 위반: {r.stdout}\n{r.stderr}"
        r = _run("pipeline/validate_relations.py", vault=vault)
        assert r.returncode == 0, f"validate_relations 위반: {r.stdout}\n{r.stderr}"

        # 원문 차단 정책 (source/* 직접조회 차단)
        sys.path.insert(0, str(KIT / "kitlib"))
        os.environ["OWNTOLOGY_VAULT"] = str(vault)
        import security
        assert security.is_folder_blocked("source/kakao/x.json")
        assert security.is_folder_blocked("source/safari-tabs/raw/y.md")
        assert not security.is_folder_blocked("people/z.md")

    print("OK — 파이프라인 스모크 테스트 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
