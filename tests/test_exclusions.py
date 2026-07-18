#!/usr/bin/env python3
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

from kitlib import config as kitconfig


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        vault = root / "vault"
        source = vault / "source" / "kakao"
        source.mkdir(parents=True)
        config_file = root / "config.json"
        config_file.write_text(json.dumps({
            "vault_path": str(vault),
            "sources": {"kakao": True, "sms": False},
            "kakao": {
                "room_names": {"chat-42": "비밀방"},
                "exclude_rooms": ["chat-42"],
                "chat_aliases": {"옛비밀방": "비밀방"},
            },
        }, ensure_ascii=False), encoding="utf-8")

        old_config_file = kitconfig.CONFIG_FILE
        old_vault = os.environ.get("OWNTOLOGY_VAULT")
        kitconfig.CONFIG_FILE = config_file
        os.environ["OWNTOLOGY_VAULT"] = str(vault)
        try:
            from web import server

            old_run = server.subprocess.run
            server.subprocess.run = lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], 0, stdout=f"{vault}/\n", stderr="")
            try:
                assert server.select_vault_folder()["path"] == str(vault)
            finally:
                server.subprocess.run = old_run

            applied = {"42": "비밀방", "43": "공개방"}
            (source / ".room_names_applied.json").write_text(
                json.dumps(applied, ensure_ascii=False), encoding="utf-8")
            rooms = {room["chat_id"]: room for room in server.get_rooms()}
            assert rooms["42"]["excluded"], "chat- 접두사 제외설정이 웹에 체크되지 않음"
            assert rooms["42"]["override"] == "비밀방"
            assert rooms["42"]["aliases"] == ["옛비밀방"]

            server.save_rooms({"room_names": {"42": "새비밀방"},
                               "exclude_rooms": ["chat-43"]})
            saved_cfg = kitconfig.load()
            assert saved_cfg["kakao"]["exclude_rooms"] == ["43"]
            assert saved_cfg["kakao"]["chat_aliases"]["비밀방"] == "새비밀방"

            payload = json.dumps({"me": {"name": "테스트"}})
            merged = kitconfig._merge(kitconfig.load(), json.loads(payload))
            kitconfig.save(merged)
            saved_cfg = kitconfig.load()
            assert saved_cfg["sources"]["kakao"] is True
            assert saved_cfg["kakao"]["chat_aliases"]["비밀방"] == "새비밀방"

            db = root / "archive.sqlite3"
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE messages (chat_id TEXT, chat_name TEXT, "
                "sender_nickname TEXT, timestamp TEXT, text TEXT, message_id INTEGER, "
                "chat_type TEXT)"
            )
            con.executemany(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("42", "비밀방", "상대", "2026-07-18T00:00:00+00:00", "비밀", 1, "group"),
                    ("43", "공개방", "상대", "2026-07-18T00:00:00+00:00", "공개", 2, "group"),
                ],
            )
            con.commit()
            con.close()

            import collectors.kakao_export as kakao

            kakao.ARCHIVE_DB = db
            kakao.KAKAO_SOURCE = source
            kakao.KAKAO_OUT = vault / "conversations" / "kakao"
            kakao.KAKAO_HASHES = source / ".hashes.json"
            kakao.ROOM_NAMES_APPLIED = source / ".room_names_applied.json"
            kakao.CONTACTS_MAP = source / ".contacts.json"
            kakao.LOG_FILE = vault / ".sync.log"

            old_argv = sys.argv
            sys.argv = ["kakao_export.py", "--no-katok-sync", "--no-sync",
                        "--chat-id", "42=비밀방", "--chat-id", "43=공개방"]
            try:
                assert kakao.main() == 0
            finally:
                sys.argv = old_argv

            assert not (source / "kmsg-katok-id-43.json").exists(), \
                "제외된 방의 신규 snapshot이 생성됨"
            assert (source / "kmsg-katok-id-42.json").exists(), \
                "비제외 방 snapshot이 생성되지 않음"

            import pipeline.sync_notes as sync_notes
            assert sync_notes.sync_kakao() == 1
            note = vault / "conversations" / "kakao" / "새비밀방.md"
            assert note.exists()
            note_text = note.read_text(encoding="utf-8")
            assert 'aliases: ["비밀방", "옛비밀방"]' in note_text
        finally:
            kitconfig.CONFIG_FILE = old_config_file
            if old_vault is None:
                os.environ.pop("OWNTOLOGY_VAULT", None)
            else:
                os.environ["OWNTOLOGY_VAULT"] = old_vault

    print("OK — 웹 제외설정 및 카카오 신규 export 차단 검사 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
