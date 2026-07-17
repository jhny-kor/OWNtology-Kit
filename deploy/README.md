# 클라우드 이관 가이드 (원격 MCP 서버)

로컬 Mac에서 만든 볼트를 클라우드 서버(OCI·AWS·기타 Linux)로 옮겨, 어디서든 LLM이
접근하게 하는 방법입니다. **볼트에는 평문 개인정보가 있으므로**(PRIVACY.md) 반드시
HTTPS + 토큰 + 접근통제를 갖춘 뒤 노출하세요.

## 구성 개요

```
[Mac: 수집·온톨로지화]  --(rsync/git, 단방향)-->  [클라우드: MCP http 서버]
                                                        │
                                          [리버스 프록시(HTTPS) 또는 Cloudflare Tunnel]
                                                        │
                                     ChatGPT · Claude · Gemini (Bearer 토큰)
```

수집·온톨로지화는 계속 **로컬 Mac**에서 돌리고(원본 앱 접근이 Mac에만 있으므로),
클라우드에는 **완성된 볼트만** 동기화해 읽기/쓰기 MCP로 노출하는 구조를 권장합니다.

## 1) 볼트 동기화 (Mac → 클라우드, 단방향)

```bash
# Mac에서 주기 실행 (cron/launchd). --delete 는 원격을 로컬 미러로 유지.
rsync -az --delete "~/Documents/my-owntology/" user@server:/home/user/owntology-vault/
```

LLM이 클라우드에서 쓴 노트를 Mac으로 되돌리려면 별도 역방향 동기화가 필요합니다
(충돌 방지를 위해 쓰기는 한쪽만 권장 — 기본은 Mac이 정본).

## 2) 클라우드에 킷 설치 + MCP 서버 실행

```bash
git clone https://github.com/jhny-kor/OWNtology-Kit.git && cd OWNtology-Kit
python3 -m venv .venv && .venv/bin/pip install -r requirements-mcp.txt

export OWNTOLOGY_VAULT=/home/user/owntology-vault
export OWNTOLOGY_TOKEN=$(openssl rand -hex 24)   # 이 값을 클라이언트에 Bearer로 사용
export MCP_TRANSPORT=streamable-http OWNTOLOGY_HOST=127.0.0.1 OWNTOLOGY_PORT=7334
export OWNTOLOGY_PUBLIC_HOST=mcp.example.com
.venv/bin/python mcp_server.py
```

상시 구동은 [`owntology-mcp.service`](owntology-mcp.service) systemd 템플릿을 사용하세요.

## 3) HTTPS 노출 — 두 가지 방법

### A. Cloudflare Tunnel (고정 IP·포트개방 불필요, 권장)

```bash
cloudflared tunnel login
cloudflared tunnel create owntology
# ~/.cloudflared/config.yml
#   tunnel: <TUNNEL_ID>
#   ingress:
#     - hostname: mcp.example.com
#       service: http://127.0.0.1:7334
#     - service: http_status:404
cloudflared tunnel route dns owntology mcp.example.com
cloudflared tunnel run owntology
```

### B. nginx 리버스 프록시 (+ Let's Encrypt)

```nginx
server {
  server_name mcp.example.com;
  location /mcp {
    proxy_pass http://127.0.0.1:7334;
    proxy_set_header Host $host;
    proxy_set_header Authorization $http_authorization;
    # 선택: ChatGPT/특정 IP만 허용
    # allow 1.2.3.4; deny all;
  }
  # certbot 이 443/TLS 블록을 채웁니다
}
```

두 방법 모두 서버는 `OWNTOLOGY_PUBLIC_HOST=mcp.example.com` 이어야 DNS rebinding 보호를
통과합니다(그 도메인을 허용목록에 추가함).

## 4) 클라이언트 연결 (원격, Bearer 토큰)

MCP 엔드포인트: `https://mcp.example.com/mcp` · 헤더: `Authorization: Bearer <OWNTOLOGY_TOKEN>`

- **Claude Desktop/Code**: 커스텀 커넥터(streamable-http) URL + Authorization 헤더
- **ChatGPT**: 커스텀 커넥터/Actions 에 위 URL·헤더 등록
- **Gemini CLi 등**: streamable-http MCP 지원 클라이언트에 동일 등록

토큰 없는 요청은 **public 티어**(비민감 프로젝트·토픽·통계만), 유효 토큰은 **owner 티어**
(카카오·인물·관계 등 전체 + 쓰기 도구)로 승격됩니다. 토큰을 못 넣는 요청은 401.

## 보안 체크리스트

- [ ] HTTPS로만 노출 (평문 HTTP 금지)
- [ ] `OWNTOLOGY_TOKEN` 16자 이상 무작위, 유출 시 즉시 교체
- [ ] 가능하면 IP 허용목록으로 접근 제한
- [ ] 서버 디스크 암호화, 볼트 접근 권한 최소화
- [ ] 감사 로그(`<VAULT>/.mcp-logs/audit.log`) 주기 점검
