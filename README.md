# owntology-kit

내 **카카오톡 · 문자(SMS/iMessage) · 메일 · Apple 메모 · Safari 탭 · GitHub 스타(선택)** 를
수집해서 개인 온톨로지 볼트(마크다운 지식 저장소)로 만들어 주는 킷입니다.
owntology 개인 볼트 파이프라인을 누구나 쓸 수 있게 설정 분리한 버전입니다.

- 로컬 전용 — 수집 데이터는 기기를 떠나지 않습니다(자세히: [PRIVACY.md](PRIVACY.md))
- 외부 pip 의존성 **0개** (Python 3.11+ stdlib만)
- 개인값(닉네임·계정·경로)은 전부 `config.json` — 웹 화면에서 입력
- **수집 소스 기본값은 전부 꺼짐** — 웹에서 명시적으로 켜야 수집합니다(암묵적 대량 수집 방지)
- 자동 추출은 전부 `extraction: auto` / `status: proposed` — 확정은 사용자 몫

> ⚠️ **개인정보**: 볼트에는 카카오톡·문자·메일 등 원문이 **평문**으로 저장되며, 대화 상대 등
> 제3자의 개인정보가 포함됩니다. 개인용으로만 쓰고 공유/공개 폴더에 두지 마세요. [PRIVACY.md](PRIVACY.md) · [SECURITY.md](SECURITY.md)

## 빠른 시작

```bash
python3 kit.py init      # ① config.json 생성 + 볼트 폴더 스캐폴드
python3 kit.py web       # ② http://127.0.0.1:8765 에서 설정 입력 (닉네임·수집 소스 켜기)
python3 kit.py doctor    # ③ 환경·권한·설정 사전 점검 (FAIL 있으면 먼저 해결)
python3 kit.py run       # ④ 수집 + 온톨로지화 원터치
python3 kit.py web       # ⑤ 인물 관계·전화·방 이름 등 수동필드 입력
```

기본 소스가 전부 꺼져 있으므로 ②에서 켜지 않으면 ④는 아무것도 수집하지 않습니다.
이후 갱신은 `python3 kit.py run` 만 반복하면 됩니다(멱등).
카카오 DB 복호화(katok sync)가 오래 걸리면 `--fast-kakao` 로 직전 아카이브에서 export만 합니다.
카카오 수집은 본인 닉네임이 없으면 건너뜁니다(본인 메시지 "나" 매핑에 필요).
수집이 끝나면 **성공/실패/건너뜀 요약**이 출력되고, 시도한 핵심 수집기가 모두 실패하면 종료 코드가 0이 아닙니다.

## 사전 요구사항 (macOS)

| 소스 | 필요한 것 |
|------|-----------|
| 카카오톡 | `katok` CLI — [NomaDamas/katok](https://github.com/NomaDamas/katok) (`cargo install katok`). 로컬 카카오톡 SQLCipher DB를 복호화해 전체 이력을 아카이브. `katok sync --source macos` 지원 버전 필요 |
| SMS/iMessage | `imsg` CLI — [openclaw/imsg](https://github.com/openclaw/imsg) 또는 [moltbot/imsg](https://github.com/moltbot/imsg) (`imsg chats --json` · `imsg history --chat-id … --json` 서브커맨드 제공) + 터미널 **Full Disk Access** (시스템 설정 > 개인정보 보호 및 보안) |
| 메일 | Mail.app 실행 중 + 첫 실행 시 **자동화(Automation) 권한** 허용 (외부 도구 불필요, osascript 내장) |
| Apple 메모 | Notes.app + 첫 실행 시 자동화 권한 허용 (외부 도구 불필요) |
| Safari 탭 | iCloud 탭 동기화 켜짐 + 터미널 Full Disk Access (외부 도구 불필요, `CloudTabs.db` 직접 읽기) |
| GitHub 스타 | `config.json`에 사용자명만 (공개 스타라 토큰 불필요, rate limit 시 `GITHUB_TOKEN` 환경변수) |

카카오·SMS만 서드파티 CLI가 필요하고(각 저장소 설치 안내 참고), 나머지는 macOS 내장 기능으로 동작합니다.
없거나 권한이 없는 소스는 **경고만 남기고 건너뜁니다** — 되는 것부터 수집됩니다.
웹 설정 화면에서 소스별 on/off 가능.

## 무엇이 만들어지나

```
<볼트>/
  source/            수집 원문 (kakao·sms·email·apple-notes·safari-tabs) — 검색 제외
  conversations/     대화 노트 (카카오 방별 / SMS 상대별 / 메모별)
  people/            인물 엔티티 — 1:1 카톡 상대 자동 스텁, 관계·전화는 웹에서 입력
  knowledge/
    links/nodes/     카카오+Safari 링크 통합 노드 (og:description 자동 요약)
    github-stars/    스타 레포별 노트
  decisions/ events/ preferences/   personal-layer 후보 승인 시 승격되는 사실 노트
  ontology/          personal-layer 후보 리포트 (전부 proposed)
  indexes/           관계·대화·토픽 인덱스 (JSON)
  quarantine/        파싱 실패 격리
  schemas/           엔티티/관계 frontmatter 스키마 (validate가 검증)
```

## 사용자 직접입력 필드

자동 수집이 못 채우는 필드는 **빈 값으로 생성**되고, `python3 kit.py web` 에서 입력합니다.

- **설정 탭**: 카카오 닉네임(필수 — 본인 메시지 "나" 매핑), 볼트 경로,
  **수집 소스 on/off**(카카오·SMS·메일·메모·Safari·GitHub),
  **온톨로지화 단계 on/off**(인물 스텁·링크 노드·개인계층 후보·일일 롤업 — 핵심 정제·검증은 항상 실행),
  나와의 채팅 chat_id, GitHub 사용자명·자기소개
- **사람 탭**: 인물별 관계(relationship)·전화·별칭 — 저장하면 `extraction: confirmed`
- **채팅방 탭**: 자동 명명된 카카오 방 이름 변경 → 다음 `run`에서 노트 이름 반영·구노트 정리

## LLM 요약 (선택)

`ANTHROPIC_API_KEY` 또는 `OPENAI_API_KEY` 환경변수가 있으면 GitHub 스타 노트에
학습 요약을 추가합니다(`--enrich`). 이때만 `pip install anthropic`(또는 `openai`)이 필요합니다.
API 키는 웹 화면에 입력하지 않습니다 — 환경변수로만.

## 개인계층 후보 승인

`ontology/personal-layer-candidates-*.md` 리포트의 decision/event/preference 후보는
전부 `status: proposed` 입니다. 승인하려면 `indexes/personal_layer_ledger.json` 의 해당
fingerprint 항목 status를 `confirmed`로 바꾸고 해당 노트를 decisions/ 등에 작성하세요
(자동 확정은 하지 않습니다).

## 자동 실행 (선택)

launchd로 주기 실행하려면:

```xml
<!-- ~/Library/LaunchAgents/com.owntology-kit.run.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.owntology-kit.run</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/python3</string>
         <string>/절대경로/owntology kit/kit.py</string><string>run</string></array>
  <key>StartInterval</key><integer>21600</integer><!-- 6시간 -->
</dict></plist>
```

`launchctl load ~/Library/LaunchAgents/com.owntology-kit.run.plist`
(launchd로 도는 프로세스에도 Full Disk Access가 필요합니다 — python3에 권한 부여)

## 구조

```
kit.py               CLI: init | doctor | collect | ontologize | run | web
config.json          사용자 설정 (init이 생성, 웹에서 편집, .gitignore 대상)
kitlib/              config·vault·security·kakao 공용 모듈
collectors/          소스별 수집기 6개
pipeline/            원문→노트 변환 + 엔티티/관계/인덱스/검증 체인
schemas/             ontology.schema.json / relation.schema.json
web/                 설정·수동입력 웹 화면 (127.0.0.1 전용)
PRIVACY.md SECURITY.md LICENSE
```

## 알려진 제한 · 로드맵

현재 미구현(기여 환영):

- 소스별 **수집 기간·특정 대화방/계정 선택** (지금은 소스 단위 on/off만)
- 원문 **선택 삭제·보존기간(retention)** 설정
- 최초 실행 **동의 마법사**
- 샘플 데이터 기반 자동 테스트
