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

## 🤖 Codex·Claude Code로 설치하기

OWNtology-Kit을 직접 설치하기 어렵다면, **Codex CLI** 또는 **Claude Code**에 아래 프롬프트를 전달해 설치를 진행할 수 있습니다.

> 실제 개인정보 수집은 사용자가 수집 대상을 직접 선택하고 동의한 이후에만 진행하도록 구성된 프롬프트입니다.

<details>
<summary><strong>Codex·Claude Code 설치 프롬프트 펼쳐 보기</strong></summary>

<br>

아래 내용을 전체 복사해 Codex 또는 Claude Code에 입력하세요.

---

당신은 macOS 로컬 환경에서 GitHub 저장소를 안전하게 설치하고 검증하는 개발 도우미입니다.

다음 저장소를 현재 사용자 PC에 설치하고, 사용자가 자신의 개인 데이터를 수집할 수 있도록 초기 설정까지 진행해 주세요.

저장소:

```text
https://github.com/jhny-kor/OWNtology-Kit
```

## 목표

* 저장소를 사용자 홈 디렉터리 아래 적절한 위치에 clone
* 실행 환경과 필수 도구 점검
* OWNtology-Kit 초기화
* 웹 설정 화면 실행 준비
* 사용자가 직접 개인정보 수집 소스를 선택하도록 안내
* 설정 완료 후 doctor 검사와 테스트 실행
* 실제 데이터 수집은 사용자의 명시적 선택 이후에만 실행
* 설치 결과와 남은 조치사항을 명확히 보고

## 중요 조건

1. 이 프로젝트는 macOS 로컬 사용을 기준으로 판단하고 진행하세요.
2. 사용자 동의 없이 카카오톡, 문자, 메일, Apple 메모, Safari 탭을 수집하지 마세요.
3. 수집 소스는 기본적으로 모두 꺼진 상태를 유지하세요.
4. `config.json`이나 개인 볼트 내용을 Git에 커밋하지 마세요.
5. 볼트 경로를 iCloud Drive, Dropbox, Google Drive, OneDrive 등 동기화 폴더에 생성하지 마세요.
6. 기존 설치나 기존 `config.json`, 기존 볼트가 있으면 삭제하거나 덮어쓰지 말고 먼저 상태를 확인하세요.
7. `sudo`는 반드시 필요한 경우에만 사용하고, 사용 전 이유를 설명하세요.
8. API 키, 비밀번호, 인증 토큰을 출력하거나 파일에 직접 기록하지 마세요.
9. 실제 개인정보 수집 전 어떤 데이터가 평문으로 저장되는지 반드시 사용자에게 알리세요.
10. 설치 과정에서 오류가 발생하면 임의로 우회하지 말고 원인, 영향, 해결 방법을 설명하세요.
11. 사용자의 기존 Git 변경사항을 임의로 reset, checkout, stash, 삭제하지 마세요.
12. 개인정보가 포함된 파일의 본문을 터미널이나 최종 보고서에 출력하지 마세요.

## 권장 설치 위치

```text
~/Developer/OWNtology-Kit
```

해당 경로가 이미 존재하면 현재 상태를 먼저 확인하고 안전하게 재사용하세요.

---

## 1. 시스템 확인

다음을 확인하세요.

* 운영체제가 macOS인지
* macOS 버전
* CPU 아키텍처가 Apple Silicon인지 Intel인지
* Python 버전이 3.11 이상인지
* Git 설치 여부
* Homebrew 설치 여부
* 현재 사용 중인 셸
* 설치 대상 경로 존재 여부
* 기존 `config.json` 존재 여부
* 기존 개인 볼트 존재 여부

예시 명령:

```bash
sw_vers
uname -m
python3 --version
git --version
brew --version
echo "$SHELL"
ls -la ~/Developer/OWNtology-Kit 2>/dev/null
```

macOS가 아니라면 설치를 계속 진행하지 말고, 현재 OWNtology-Kit의 주요 데이터 수집 기능이 macOS 환경을 요구한다고 설명하세요.

Python 3.11 이상이 없다면 Homebrew가 설치된 경우 Homebrew로 설치하세요.

```bash
brew install python@3.12
```

설치 후 실제 사용할 Python 경로와 버전을 확인하세요.

```bash
which python3
python3 --version
```

Homebrew Python이 기본 PATH에 잡히지 않는다면 다음 경로도 확인하세요.

Apple Silicon:

```bash
/opt/homebrew/bin/python3 --version
```

Intel Mac:

```bash
/usr/local/bin/python3 --version
```

---

## 2. 저장소 설치

저장소가 없다면 clone하세요.

```bash
mkdir -p ~/Developer
cd ~/Developer
git clone https://github.com/jhny-kor/OWNtology-Kit.git
cd OWNtology-Kit
```

이미 저장소가 있다면 먼저 다음을 확인하세요.

```bash
cd ~/Developer/OWNtology-Kit
git status
git remote -v
git branch --show-current
git log -1 --oneline
```

사용자의 변경사항이 있으면 `git pull`, `git reset`, `git checkout`, `git clean`을 임의로 실행하지 마세요.

변경사항이 없고 원격 저장소가 올바른 경우에만 최신 `main` 브랜치로 업데이트하세요.

```bash
git pull --ff-only origin main
```

`--ff-only`로 업데이트할 수 없다면 강제로 병합하지 말고 사용자에게 상태를 보고하세요.

---

## 3. 저장소 안전성 확인

다음을 확인하세요.

```bash
git status
git ls-files config.json
git check-ignore -v config.json
test -f LICENSE && echo "LICENSE 있음"
test -f PRIVACY.md && echo "PRIVACY.md 있음"
test -f SECURITY.md && echo "SECURITY.md 있음"
```

확인 목표:

* `config.json`이 Git 추적 대상이 아닌지
* `config.json`이 `.gitignore`에 포함되어 있는지
* 개인 데이터가 저장소 내부에 생성되지 않는지
* `LICENSE`, `PRIVACY.md`, `SECURITY.md`가 존재하는지

저장소에 실제 사용자의 다음 정보가 포함된 파일이 발견되면 설치를 중단하고 보고하세요.

* 실제 카카오톡 대화
* 실제 SMS/iMessage
* 실제 메일 본문
* 실제 전화번호
* 실제 주소
* API 키
* 인증 토큰
* 비밀번호
* 개인 볼트
* 개인용 `config.json`

단, 테스트 코드에 포함된 명백한 가상 샘플 데이터는 실제 개인정보와 구분하세요.

개인정보나 시크릿을 검사할 때 파일 전체 내용을 출력하지 말고, 파일 경로와 문제 유형만 보고하세요.

---

## 4. 초기화

다음을 실행하세요.

```bash
python3 kit.py init
```

사용 중인 Python이 `python3`가 아니라 별도 Homebrew 경로라면 정확한 실행 파일을 사용하세요.

예:

```bash
/opt/homebrew/bin/python3 kit.py init
```

초기화 후 다음을 확인하세요.

* `config.json` 생성 여부
* 기본 볼트 경로
* 볼트 디렉터리 생성 여부
* 모든 수집 소스가 `false`인지
* 볼트 경로가 클라우드 동기화 경로가 아닌지

`config.json`의 실제 값을 검사하되 API 키나 개인정보가 있으면 출력하지 마세요.

필요한 경우 다음처럼 수집 소스 상태만 안전하게 확인하세요.

```bash
python3 - <<'PY'
import json
from pathlib import Path

p = Path("config.json")
if not p.exists():
    raise SystemExit("config.json 없음")

cfg = json.loads(p.read_text(encoding="utf-8"))
print("vault_path:", cfg.get("vault_path"))
print("sources:", cfg.get("sources"))
PY
```

기본 볼트 경로는 다음입니다.

```text
~/Documents/my-owntology
```

사용자의 `Documents` 폴더가 iCloud Drive에 동기화되는 환경이면 다음처럼 로컬 전용 경로를 권장하세요.

```text
~/OWNtology/my-owntology
```

경로를 변경할 때는 사용자의 동의를 받은 후 `config.json`에 반영하세요.

기존 볼트가 존재하면 삭제하거나 초기화하지 말고 다음을 확인하세요.

* 기존 파일 개수
* 주요 폴더 존재 여부
* 마지막 수정 시간
* 기존 사용 데이터가 있는지

기존 파일의 개인정보 본문은 출력하지 마세요.

---

## 5. 사전 환경 검사

다음을 실행하세요.

```bash
python3 kit.py doctor
```

결과를 다음 상태로 구분해 정리하세요.

* `OK`
* `WARN`
* `FAIL`

`FAIL`이 있으면 실제 데이터 수집을 진행하지 마세요.

`WARN`이 있으면 다음 사항을 구분하세요.

* 실제 수집을 막는 경고
* 사용자가 인지하면 되는 개인정보 경고
* 특정 소스를 사용하지 않으면 무시할 수 있는 경고

---

## 6. 선택 기능별 도구 설치

사용자가 활성화하려는 기능에 필요한 도구만 설치하세요.

모든 수집 도구를 일괄 설치하지 마세요.

### 6.1 카카오톡 수집

필요한 항목:

* Rust와 Cargo
* `katok` CLI
* 사용자의 카카오톡 프로필 닉네임
* macOS 카카오톡 로컬 데이터 접근 가능 환경

먼저 Rust 설치 여부를 확인하세요.

```bash
rustc --version
cargo --version
```

Cargo가 없다면 Homebrew 설치를 우선 고려하세요.

```bash
brew install rust
```

그다음 저장소 README에서 요구하는 방식과 `katok` 공식 저장소의 최신 설치 방법을 확인해 설치하세요.

기본 예시:

```bash
cargo install katok
```

설치 후 확인하세요.

```bash
which katok
katok --help
```

`katok`이 설치됐지만 PATH에서 찾지 못한다면 다음 경로를 확인하세요.

```bash
ls -la ~/.cargo/bin/katok
```

`~/.cargo/bin`이 PATH에 없다면 현재 셸 설정 파일에 중복 없이 추가하세요.

zsh를 사용하는 경우 일반적으로 다음 파일을 확인하세요.

```text
~/.zshrc
~/.zprofile
```

추가 예시:

```bash
export PATH="$HOME/.cargo/bin:$PATH"
```

셸 설정 파일을 수정했다면 중복 항목을 만들지 말고, 변경 내용을 사용자에게 보고하세요.

카카오 수집을 켜기 전에 반드시 사용자의 정확한 카카오톡 프로필 닉네임을 입력하도록 안내하세요.

닉네임을 추측하지 마세요.

카카오 수집을 시작하기 전에 사용자가 제외할 대화방을 설정하도록 안내하세요.

### 6.2 SMS/iMessage 수집

필요한 항목:

* OWNtology-Kit이 지원하는 `imsg` CLI
* 터미널 또는 Codex·Claude Code를 실행하는 앱의 Full Disk Access 권한

저장소 README에서 지원하는 `imsg` 구현체와 명령 형식을 확인한 뒤 설치하세요.

설치 후 다음 명령 형식이 동작하는지 확인하세요.

```bash
imsg chats --json
```

권한 오류가 발생하면 사용자에게 다음 경로를 안내하세요.

```text
시스템 설정
→ 개인정보 보호 및 보안
→ 전체 디스크 접근 권한
→ Terminal, iTerm2, Codex 또는 Claude Code를 실행 중인 앱 허용
```

권한을 변경한 후에는 해당 앱을 완전히 종료하고 다시 실행해야 한다고 안내하세요.

실제 메시지 내용은 출력하지 마세요.

명령 테스트가 필요하면 채팅 개수나 성공 여부만 확인하세요.

### 6.3 Mail.app 수집

별도 외부 도구는 설치하지 마세요.

다음을 안내하세요.

* Mail.app에 사용자의 메일 계정이 등록되어 있어야 함
* Mail.app을 한 번 실행해야 함
* 첫 수집 시 자동화 권한 요청을 허용해야 함
* 최근 메일의 제목과 본문이 로컬 볼트에 평문으로 저장될 수 있음

자동화 권한을 거부했다면 다음 위치에서 다시 설정할 수 있다고 안내하세요.

```text
시스템 설정
→ 개인정보 보호 및 보안
→ 자동화
→ 사용 중인 터미널 또는 개발 도구
→ Mail 허용
```

### 6.4 Apple 메모 수집

별도 외부 도구는 설치하지 마세요.

다음을 안내하세요.

* Notes.app이 정상 실행돼야 함
* 첫 실행 시 자동화 권한 허용 필요
* 메모 제목과 본문이 로컬 볼트에 평문으로 저장될 수 있음

자동화 설정 위치:

```text
시스템 설정
→ 개인정보 보호 및 보안
→ 자동화
→ 사용 중인 터미널 또는 개발 도구
→ Notes 허용
```

### 6.5 Safari 탭 수집

다음을 확인하세요.

* Safari iCloud 탭 동기화 활성화
* Full Disk Access 권한
* `CloudTabs.db` 접근 가능 여부

예시 확인:

```bash
ls -l ~/Library/Containers/com.apple.Safari/Data/Library/Safari/CloudTabs.db
```

해당 파일의 내용을 직접 출력하지 마세요.

Safari 탭 URL과 제목이 로컬 볼트에 저장될 수 있음을 안내하세요.

### 6.6 GitHub Stars 수집

사용자의 GitHub 사용자명을 직접 입력받으세요.

공개 Stars만 수집할 경우 토큰은 기본적으로 요구하지 마세요.

Rate limit 문제가 발생할 때만 `GITHUB_TOKEN` 환경변수 사용 방법을 안내하세요.

토큰을 다음 위치에 저장하지 마세요.

* `config.json`
* README
* 셸 히스토리에 그대로 남을 수 있는 명령
* Git 추적 파일

가능하면 현재 셸 세션의 환경변수나 안전한 비밀 관리 방식을 사용하세요.

---

## 7. 웹 설정 화면 실행

다음을 실행하세요.

```bash
python3 kit.py web
```

웹 화면 주소:

```text
http://127.0.0.1:8765
```

가능하다면 다음 명령으로 로컬 브라우저를 여세요.

```bash
open http://127.0.0.1:8765
```

다음 내용을 사용자가 직접 설정하도록 안내하세요.

* 이름
* 카카오톡 프로필 닉네임
* 개인 볼트 경로
* 활성화할 수집 소스
* GitHub 사용자명
* 카카오톡 나와의 채팅 `chat_id`
* 온톨로지화 단계
* 제외할 카카오톡 방

웹 화면은 외부 주소에 바인딩하지 마세요.

다음과 같은 설정을 사용하지 마세요.

```bash
HOST=0.0.0.0 python3 kit.py web
```

웹 설정 서버는 인증이 없으므로 반드시 localhost에서만 사용하세요.

사용자가 설정을 완료한 후 웹 서버를 정상 종료하세요.

---

## 8. 개인정보 경고

실제 수집 전에 사용자에게 다음 내용을 명확하게 전달하세요.

* 카카오톡, 문자, 메일, Apple 메모 원문이 로컬 볼트에 평문으로 저장될 수 있음
* 대화 상대방의 이름, 전화번호, 메시지 등 제3자의 개인정보가 포함될 수 있음
* 볼트를 GitHub, 공유 폴더, NAS 공유 경로, 클라우드 동기화 폴더에 올리면 안 됨
* macOS FileVault 사용을 권장함
* 카카오 메시지의 링크 요약 기능을 켜면 메시지에 포함된 URL로 외부 HTTP 요청이 발생할 수 있음
* 외부 사이트는 사용자의 IP 주소와 요청 URL을 확인할 수 있음
* 실제 수집 전 원하지 않는 카카오톡 방을 제외해야 함
* LLM enrich 기능을 사용하면 대상 데이터 일부가 설정한 외부 LLM 제공자에게 전송될 수 있음
* 수집 완료 후 필요 없는 원문은 `purge` 명령으로 삭제할 수 있음
* 완전 삭제가 필요하면 개인 볼트 폴더 자체를 삭제해야 함

사용자가 이 내용을 이해하기 전에는 실제 수집을 실행하지 마세요.

---

## 9. 설정 후 재검사

웹 설정 완료 후 다음을 다시 실행하세요.

```bash
python3 kit.py doctor
```

`FAIL`이 없을 때만 다음 단계로 진행하세요.

`WARN`은 사용자에게 설명하고 실제 영향이 있는지 판단하세요.

추가로 다음을 확인하세요.

```bash
git status
git check-ignore -v config.json
```

확인 목표:

* `config.json`이 Git 변경사항에 나타나지 않음
* 개인 볼트가 저장소 외부에 있음
* 수집 소스가 사용자의 선택과 일치함
* 카카오톡이 켜져 있다면 닉네임이 입력되어 있음
* GitHub Stars가 켜져 있다면 GitHub 사용자명이 입력되어 있음

---

## 10. 테스트 실행

외부 개인 데이터를 사용하지 않고 저장소에 포함된 스모크 테스트를 먼저 실행하세요.

```bash
python3 tests/test_pipeline.py
```

테스트 실패 시 실제 수집을 진행하지 마세요.

실패한 단계와 로그를 분석해 다음 중 어느 문제인지 구분하세요.

* 저장소 코드 문제
* Python 버전 문제
* 잘못된 실행 경로
* 파일 권한 문제
* 기존 설정과의 충돌
* 테스트 환경 문제

테스트 수정을 시도해야 한다면 기존 사용자 데이터와 무관한 테스트 코드만 수정하세요.

사용자의 명시적인 요청 없이 원격 저장소에 commit하거나 push하지 마세요.

---

## 11. 실제 수집 전 최종 확인

사용자가 활성화한 수집 소스를 다시 확인하세요.

반드시 실행 직전 다음 형식으로 사용자에게 요약하세요.

```text
활성화된 수집 소스
- 카카오톡: 켜짐/꺼짐
- SMS/iMessage: 켜짐/꺼짐
- Mail: 켜짐/꺼짐
- Apple 메모: 켜짐/꺼짐
- Safari 탭: 켜짐/꺼짐
- GitHub Stars: 켜짐/꺼짐

온톨로지화 단계
- 인물 스텁 생성: 켜짐/꺼짐
- 링크 노드 생성: 켜짐/꺼짐
- 개인계층 후보 생성: 켜짐/꺼짐
- 일일 롤업: 켜짐/꺼짐

볼트 경로:
<실제 경로>

제외된 카카오톡 방:
<목록 또는 없음>

외부 요청 가능 기능:
- 링크 노드 생성: 켜짐/꺼짐
- LLM enrich: 사용/미사용
```

사용자가 명시적으로 실제 수집을 요청한 경우에만 실행하세요.

---

## 12. 실제 수집

사용자가 동의했다면 다음을 실행하세요.

```bash
python3 kit.py run
```

카카오 아카이브가 이미 있고 DB 복호화를 생략하려는 경우에만 다음 옵션을 검토하세요.

```bash
python3 kit.py run --fast-kakao
```

`--fast-kakao`를 사용할 때는 기존 카카오 아카이브가 최신이 아닐 수 있음을 사용자에게 알리세요.

수집 결과에서 각 소스의 다음 상태를 확인하세요.

* 성공
* 실패
* 건너뜀

단순히 프로세스 종료코드만 보고 성공으로 판단하지 마세요.

모든 핵심 수집기가 건너뛰어졌거나 실제 파일이 생성되지 않았다면 완전한 수집 성공이라고 보고하지 마세요.

---

## 13. 결과 검증

수집 후 다음을 확인하세요.

* 볼트 경로가 저장소 외부인지
* `source/`에 활성화된 소스의 원문이 생성됐는지
* `conversations/`에 변환된 노트가 생성됐는지
* `people/`, `ontology/`, `indexes/`가 생성됐는지
* 저장소의 `git status`에 개인정보 파일이 나타나지 않는지
* 오류 로그와 `quarantine` 파일이 있는지
* 수집 결과 중 실패나 건너뜀이 있는지

개인정보를 출력하지 않고 파일 개수만 확인하는 예시:

```bash
find ~/Documents/my-owntology/source -type f 2>/dev/null | wc -l
find ~/Documents/my-owntology/conversations -type f 2>/dev/null | wc -l
find ~/Documents/my-owntology/people -type f 2>/dev/null | wc -l
find ~/Documents/my-owntology/ontology -type f 2>/dev/null | wc -l
find ~/Documents/my-owntology/indexes -type f 2>/dev/null | wc -l
```

볼트 경로가 변경됐다면 실제 설정된 경로를 사용하세요.

내용을 출력할 때 다음 정보는 노출하지 마세요.

* 실제 메시지
* 전화번호
* 메일 주소
* 메일 본문
* 사람 이름
* 메모 본문
* Safari URL
* API 키
* 인증 토큰

문제가 있는 경우 파일 경로, 파일 개수, 오류 유형만 보고하세요.

---

## 14. 데이터 삭제 방법 안내

사용자에게 다음 명령을 안내하세요.

### 원문 삭제 대상 미리 확인

```bash
python3 kit.py purge --raw
```

### 실제 원문 삭제

```bash
python3 kit.py purge --raw --apply
```

### 보존기간 대상 미리 확인

```bash
python3 kit.py purge --older-than 180
```

### 실제 보존기간 삭제

```bash
python3 kit.py purge --older-than 180 --apply
```

현재 보존기간 기능이 모든 JSON 원문의 메시지 단위 삭제를 보장하지 않을 수 있다고 안내하세요.

완전 삭제가 필요하면 먼저 실제 볼트 경로를 확인한 뒤 볼트 폴더 자체를 삭제하는 방법을 안내하세요.

볼트 삭제 전에는 반드시 삭제 대상 절대 경로를 사용자에게 보여주고 확인받으세요.

예시:

```bash
rm -rf ~/Documents/my-owntology
```

위 명령은 사용자 확인 없이 실행하지 마세요.

---

## 15. 자동 실행은 기본 설정하지 않기

사용자가 명시적으로 요청하지 않는 한 launchd 자동 실행을 등록하지 마세요.

자동 실행을 요청받으면 다음을 먼저 확인하세요.

* OWNtology-Kit 절대 경로
* Python 실행 파일 절대 경로
* Full Disk Access 권한
* 활성화된 수집 소스
* 실행 주기
* 로그 경로
* 개인정보 보존정책
* Mac이 잠자기 상태일 때 실행 여부
* 실패 알림 방법

자동 실행 파일에는 API 키나 개인정보를 직접 기록하지 마세요.

자동 실행을 설정한 경우 다음도 안내하세요.

* 등록 상태 확인 방법
* 로그 확인 방법
* 중지 방법
* 제거 방법

---

## 16. 기존 사용자 데이터 보호

기존 볼트가 있는 경우 다음 원칙을 지키세요.

* 기존 파일을 일괄 삭제하지 않음
* 기존 `config.json`을 덮어쓰지 않음
* 기존 방 이름 설정을 제거하지 않음
* 기존 제외 목록을 초기화하지 않음
* 기존 수동 인물 관계·별칭·전화번호를 덮어쓰지 않음
* 기존 온톨로지 노트를 임의로 재작성하지 않음
* 테스트를 기존 볼트에서 실행하지 않음
* 테스트는 반드시 임시 디렉터리에서 실행

설치 업데이트가 필요한 경우 먼저 다음을 보고하세요.

```text
현재 저장소 상태
- 브랜치:
- 최신 커밋:
- 로컬 변경사항:
- 기존 config.json:
- 기존 볼트:
- 업데이트 가능 여부:
```

---

## 17. 오류 대응 원칙

오류가 발생하면 다음 순서로 대응하세요.

1. 실패한 명령 확인
2. 종료코드 확인
3. 표준 오류와 마지막 로그 확인
4. 사용자 환경 문제인지 코드 문제인지 구분
5. 개인정보가 포함된 로그는 마스킹
6. 기존 데이터를 훼손하지 않는 해결책 우선 적용
7. 수정 후 동일 검사 재실행
8. 해결되지 않으면 정확한 원인과 다음 조치 보고

다음 행동을 임의로 하지 마세요.

* 저장소 전체 reset
* 사용자 볼트 삭제
* `config.json` 삭제
* 권한을 과도하게 변경
* 모든 파일에 `chmod 777`
* 출처가 불명확한 스크립트 실행
* API 키를 명령줄에 직접 노출
* 사용자의 동의 없는 실제 데이터 수집

---

## 18. 최종 보고서

작업 완료 후 다음 형식으로 보고하세요.

```text
OWNtology-Kit 설치 결과

설치 경로:
...

저장소 브랜치 및 커밋:
...

macOS:
...

CPU:
...

Python:
...

볼트 경로:
...

활성화된 수집 소스:
- 카카오톡:
- SMS/iMessage:
- Mail:
- Apple 메모:
- Safari 탭:
- GitHub Stars:

온톨로지화 단계:
- 인물 스텁:
- 링크 노드:
- 개인계층 후보:
- 일일 롤업:

설치된 추가 도구:
- katok:
- imsg:
- Rust/Cargo:
- 기타:

doctor 결과:
- OK:
- WARN:
- FAIL:

스모크 테스트:
- 성공/실패
- 실패 시 원인:

실제 수집:
- 실행함/실행하지 않음

소스별 수집 결과:
- 카카오톡:
- SMS/iMessage:
- Mail:
- Apple 메모:
- Safari:
- GitHub Stars:

생성 결과:
- source 파일 수:
- conversations 파일 수:
- people 파일 수:
- ontology 파일 수:
- indexes 파일 수:

보안 확인:
- config.json Git 제외:
- 볼트 저장소 외부 위치:
- 클라우드 동기화 경로 여부:
- 웹 서버 루프백 제한:
- 개인 데이터 Git 변경사항 여부:
- 개인정보 평문 저장 안내 완료:
- 외부 HTTP 요청 기능 안내 완료:

실패하거나 건너뛴 항목:
...

사용자가 추가로 해야 할 작업:
1.
2.
3.
```

## 최종 판단 기준

* 설치 명령이 성공했더라도 실제 데이터가 생성되지 않았다면 수집 완료라고 단정하지 마세요.
* 사용자가 수집 소스를 선택하지 않았다면 설치와 테스트만 완료하고 실제 수집은 하지 마세요.
* 개인정보가 포함될 가능성이 있는 명령은 실행 목적과 저장 위치를 먼저 설명하세요.
* 기존 데이터와 사용자 설정을 최우선으로 보호하세요.
* 수집 결과에서 모든 항목이 실패하거나 건너뛰었다면 성공으로 보고하지 마세요.
* 실제 수집을 실행하지 않은 경우에도 설치, doctor, 테스트 결과는 명확히 구분해 보고하세요.
* 사용자의 명시적인 요청 없이 Git commit, Git push, pull request 생성을 하지 마세요.

---

</details>


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
웹 설정 화면에서 소스별 on/off 가능. 실행 전 `python3 kit.py doctor` 로 설치·권한 상태를 점검하세요.

## 문제 해결 (Troubleshooting)

`kit.py doctor` 가 대부분의 원인을 짚어 줍니다. 자주 겪는 경우:

| 증상 | 원인 · 해결 |
|------|-------------|
| `katok not on PATH` | katok 미설치. [NomaDamas/katok](https://github.com/NomaDamas/katok) 설치 후 `~/.cargo/bin` 을 PATH에 추가 |
| `imsg not found` | imsg 미설치. [openclaw/imsg](https://github.com/openclaw/imsg) 설치 |
| SMS `cannot read chat.db` | 터미널에 **Full Disk Access** 부여 (시스템 설정 > 개인정보 보호 및 보안 > 전체 디스크 접근 권한 → 터미널 추가 후 재시작) |
| 메일/메모 수집 시 빈 결과 | 첫 실행 시 뜨는 **자동화 권한 팝업**을 허용해야 함. 거부했다면 시스템 설정 > 개인정보 보호 및 보안 > 자동화에서 터미널→Mail/Notes 허용 |
| Safari 탭 `CloudTabs.db 접근 불가` | iCloud 탭 동기화 켜기 + 터미널 Full Disk Access |
| 카카오 수집이 통째로 건너뜀 | 본인 닉네임 미설정. `kit.py web` 설정 탭에서 입력 |
| launchd 자동 실행 시 권한 오류 | GUI 세션과 별개 — `/usr/bin/python3`(또는 사용하는 python) 자체에 Full Disk Access 부여 |

특정 방을 수집에서 빼려면 `config.json` 의 `kakao.exclude_rooms` 에 방 이름이나 chat_id를 넣습니다.
수집 기간·건수는 `mail.days`/`mail.limit`/`sms.limit`/`kakao.min_messages` 로 조정합니다.

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
kit.py               CLI: init | doctor | collect | ontologize | run | web | purge
config.json          사용자 설정 (init이 생성, 웹에서 편집, .gitignore 대상)
kitlib/              config·vault·security·kakao 공용 모듈
collectors/          소스별 수집기 6개
pipeline/            원문→노트 변환 + 엔티티/관계/인덱스/검증 체인
schemas/             ontology.schema.json / relation.schema.json
web/                 설정·수동입력 웹 화면 (127.0.0.1 전용)
tests/               파이프라인 스모크 테스트
PRIVACY.md SECURITY.md LICENSE
```

## 데이터 관리 · 삭제

수집된 원문/노트는 언제든 지울 수 있습니다(기본은 dry-run, `--apply`로 실제 삭제):

```bash
python3 kit.py purge --raw               # source/ 원문 전체 삭제(대화 노트는 유지)
python3 kit.py purge --older-than 180    # 180일보다 오래된 대화/원문 노트 삭제
python3 kit.py purge --older-than 180 --apply
```

볼트 폴더를 통째로 지우면 모든 수집 데이터가 사라집니다.

## 테스트

```bash
python3 tests/test_pipeline.py    # 샘플 카카오/SMS로 파이프라인 스모크 테스트 (외부 앱 불필요)
```

## 알려진 제한 · 로드맵

기여 환영:  
  
- 소스별 **계정 선택**, 카카오 방 **화이트리스트**(현재는 제외 목록·기간·건수만 지원)
- 보존기간(retention) **자동 스케줄 적용** (현재는 `purge` 수동 실행)
- GUI 기반 최초 실행 마법사 (현재는 `web` 설정 화면 + `doctor` 점검으로 대체)
