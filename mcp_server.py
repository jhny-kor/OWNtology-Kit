#!/usr/bin/env python3
"""
owntology-kit MCP Server
- 기본: stdio 트랜스포트 (로컬, 본인=owner). 실행: python3 kit.py mcp
- 클라우드: MCP_TRANSPORT=streamable-http, 포트 OWNTOLOGY_PORT(기본 7334),
  인증 Authorization: Bearer <OWNTOLOGY_TOKEN> — deploy/README.md 참고
"""

import os
import json
import sys
from pathlib import Path
from typing import Optional

# kitlib(vault·kakao·audit·security)와 pipeline(extract_personal_layer) 임포트 경로.
_KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(_KIT / "kitlib"))
sys.path.insert(0, str(_KIT / "pipeline"))
sys.path.insert(0, str(_KIT))
from kitlib.config import vault_path as _vault_path
_VAULT = _vault_path()  # OWNTOLOGY_VAULT 환경변수 설정 (vault.py가 읽음)
# 감사 로그는 볼트 안 .mcp-logs/ 에 (env로 재지정 가능).
os.environ.setdefault("OWNTOLOGY_LOG_DIR", str(_VAULT / ".mcp-logs"))

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
import vault
import kakao
import audit
import security


def _owner_only() -> Optional[str]:
    """카카오 원문·인물 신원·후보 승인 등 owner 전용 도구 가드.
    owner 아니면 거부 JSON, owner면 None."""
    if not security.owner_active():
        return _json({"blocked": True,
                      "reason": "이 도구는 owner 인증(유효 Bearer 토큰) 세션에서만 사용 가능합니다. "
                                "프로젝트·토픽·통계 도구는 인증 없이 사용하세요."})
    return None


def _json(obj) -> str:
    """구조화 결과를 한글 보존 JSON 문자열로 직렬화."""
    return json.dumps(obj, ensure_ascii=False, indent=2)

TOKEN = os.getenv("OWNTOLOGY_TOKEN", "")
# 기본 bind를 localhost로 제한(P0-1). 외부 노출은 OWNTOLOGY_HOST 명시 설정 시에만.
_host = os.getenv("OWNTOLOGY_HOST", "127.0.0.1")
_port = int(os.getenv("OWNTOLOGY_PORT", "7334"))
# DNS rebinding 보호(SDK 기본은 localhost만 허용 → 리버스 프록시 경유 시 421).
# 클라우드에서 공개 도메인 뒤에 둘 때만 OWNTOLOGY_PUBLIC_HOST 로 그 도메인을 명시 허용.
# (기본은 빈 값 → 로컬 전용. 공개 시 접근통제는 프록시 IP 허용목록/토큰이 담당.)
_public_host = os.getenv("OWNTOLOGY_PUBLIC_HOST", "").strip()
_extra_hosts = [_public_host, f"{_public_host}:*"] if _public_host else []
_extra_origins = [f"https://{_public_host}", f"https://{_public_host}:*"] if _public_host else []
_ts = TransportSecuritySettings(
    allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", *_extra_hosts],
    allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*", *_extra_origins],
)
mcp = FastMCP("owntology", host=_host, port=_port, transport_security=_ts)

# 감사 로그(B2): mcp.tool 데코레이터를 래핑해 모든 도구 호출을 자동 기록.
# (개별 도구에 손대지 않고 한 곳에서 적용 — 아래 모든 @mcp.tool()에 audit 적용됨)
_orig_tool = mcp.tool
def _audited_tool(*targs, **tkwargs):
    register = _orig_tool(*targs, **tkwargs)
    def wrap(fn):
        return register(audit.audited("mcp")(fn))
    return wrap
mcp.tool = _audited_tool


# ── Tools ────────────────────────────────────────────────────

@mcp.tool()
def search_notes(query: str, limit: int = 8, folder: Optional[str] = None) -> str:
    """
    owntology 볼트에서 자연어 쿼리로 노트를 검색합니다.

    Args:
        query: 검색할 키워드나 문장 (한국어/영어 모두 가능)
        limit: 반환할 최대 결과 수 (기본 8)
        folder: 특정 폴더 한정 검색 (예: "projects", "conversations", "people", "source/email")
    """
    results = vault.search_notes(query, limit=limit, folder=folder)
    if not results:
        return "검색 결과 없음"
    lines = []
    routes = sorted({n["_routed"] for n in results if n.get("_routed")})
    if routes:
        lines.append(f"[구조화 조회] {' | '.join(routes)}\n")
    for i, n in enumerate(results, 1):
        lines.append(f"{i}. [{n['type']}] {n['title']}")
        lines.append(f"   path: {n['path']}")
        if n.get("summary"):
            lines.append(f"   summary: {n['summary'][:120]}")
        if n.get("topics"):
            lines.append(f"   topics: {', '.join(n['topics'])}")
        if n.get("date"):
            lines.append(f"   date: {n['date']}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def get_note(path: str) -> str:
    """
    경로로 특정 노트의 전체 내용을 읽습니다.

    Args:
        path: 볼트 내 상대 경로 (예: "projects/novalane.md")
    """
    note = vault.get_note(path)
    if not note:
        return f"노트를 찾을 수 없음: {path}"
    if note.get("_blocked"):
        return f"🔒 접근 차단: {note.get('reason', '')} (path: {note.get('path', path)})"
    body = note.get("body", "")
    fm_lines = [
        f"title: {note['title']}",
        f"type: {note['type']}",
        f"date: {note['date']}",
        f"project: {note['project']}",
        f"topics: {note['topics']}",
        f"entities: {note['entities']}",
        f"summary: {note['summary']}",
    ]
    return "---\n" + "\n".join(fm_lines) + "\n---\n" + body.strip()


@mcp.tool()
def list_notes(folder: str = "projects", limit: int = 20) -> str:
    """
    특정 폴더의 노트 목록을 반환합니다.

    Args:
        folder: 폴더 경로 (예: "projects", "people", "conversations/claude", "source/email")
        limit: 최대 반환 수 (기본 20)
    """
    notes = vault.list_notes(folder, limit=limit)
    if not notes:
        return f"{folder} 폴더에 노트 없음"
    lines = [f"[{folder}] 노트 목록 ({len(notes)}개)\n"]
    for n in notes:
        line = f"- {n['title']}"
        if n.get("date"):
            line += f" ({n['date']})"
        if n.get("summary"):
            line += f"\n  {n['summary'][:100]}"
        lines.append(line)
    return "\n".join(lines)


@mcp.tool()
def get_projects() -> str:
    """
    owntology의 모든 프로젝트 목록과 상태를 반환합니다.
    """
    projects = vault.get_projects()
    if not projects:
        return "프로젝트 없음"
    lines = [f"프로젝트 목록 ({len(projects)}개)\n"]
    for p in projects:
        status = p.get("status", "")
        release = p.get("release_status", "")
        tag = f"{status} · {release}" if release else status
        tech = p.get("tech", [])
        tech_str = ", ".join(tech) if isinstance(tech, list) else tech
        lines.append(f"- **{p['title']}** [{tag}]")
        if tech_str:
            lines.append(f"  tech: {tech_str}")
        if p.get("summary"):
            lines.append(f"  {p['summary'][:100]}")
    return "\n".join(lines)


@mcp.tool()
def get_people() -> str:
    """
    owntology의 모든 인물 목록을 반환합니다.
    owner 인증(Bearer 토큰) 세션이 아니면 실명·별칭·관계 없이 유형별 개수만
    반환됩니다(관계 API와 동일한 개인정보 정책 — 5차 P0).
    """
    if not security.owner_active():
        return _json(vault.get_people_summary())
    people = vault.get_people()
    if not people:
        return "등록된 인물 없음"
    lines = [f"인물 목록 ({len(people)}개)\n"]
    for p in people:
        rel = p.get("relationship", "")
        aliases = p.get("aliases", [])
        alias_str = " / ".join(aliases) if isinstance(aliases, list) and aliases else ""
        line = f"- **{p['title']}**"
        if rel:
            line += f" ({rel})"
        if alias_str:
            line += f" — 별명: {alias_str}"
        lines.append(line)
    return "\n".join(lines)


@mcp.tool()
def get_recent_conversations(days: int = 7) -> str:
    """
    최근 N일 대화 목록 (요약 포함).

    Args:
        days: 최근 며칠치 (기본 7)
    """
    convs = vault.get_recent_conversations(days=days)
    if not convs:
        return f"최근 {days}일 대화 없음"
    lines = [f"최근 {days}일 대화 ({len(convs)}개)\n"]
    for c in convs:
        src = c.get("source", "")
        lines.append(f"- [{src}] {c['title']}")
        lines.append(f"  date: {c['date']}  path: {c['path']}")
        if c.get("summary"):
            lines.append(f"  {c['summary'][:120]}")
    return "\n".join(lines)


@mcp.tool()
def create_note(
    title: str,
    content: str,
    folder: str = "knowledge/notes",
    topics: Optional[list] = None,
    summary: str = "",
    note_type: str = "note",
) -> str:
    """
    owntology 볼트에 새 노트를 생성합니다.

    Args:
        title: 노트 제목
        content: 노트 본문 (마크다운 형식)
        folder: 저장 폴더 (기본: knowledge/notes, 또는 projects, people, daily 등)
        topics: 관련 토픽 태그 목록
        summary: 한 줄 요약
        note_type: 노트 유형 (note, project, person 등)
    """
    from datetime import datetime
    date = datetime.now().strftime("%Y-%m-%d")
    safe_title = vault._slugify(title)
    path = f"{folder}/{date}-{safe_title}"
    try:
        note = vault.create_note(
            path=path, title=title, content=content,
            note_type=note_type, topics=topics or [],
            summary=summary, date=date,
        )
        return f"노트 생성 완료: {note['path']}"
    except FileExistsError:
        return f"이미 존재하는 노트: {path}"
    except Exception as e:
        return f"오류: {e}"


@mcp.tool()
def upload_markdown(path: str, content: str, overwrite: bool = False) -> str:
    """마크다운 파일을 원본 그대로 볼트에 업로드합니다(파일명·frontmatter 보존).
    create_note 와 달리 날짜 접두·슬러그·frontmatter 자동생성을 하지 않습니다.
    이미 frontmatter까지 갖춘 .md 를 그대로 올릴 때 사용하세요.

    Args:
        path: 저장할 볼트 내 상대 경로 (예: "knowledge/notes/내문서.md")
        content: 파일 전체 내용 (frontmatter 포함, 그대로 저장됨)
        overwrite: 기존 파일 덮어쓰기 허용 (기본 False)
    """
    try:
        res = vault.write_raw_md(path, content, overwrite=overwrite)
        verb = "덮어씀" if res["overwritten"] else "생성"
        return f"업로드 완료({verb}): {res['path']} ({res['bytes']} bytes)"
    except FileExistsError:
        return f"이미 존재함(덮어쓰려면 overwrite=true): {path}"
    except (PermissionError, ValueError) as e:
        return f"거부: {e}"
    except Exception as e:
        return f"오류: {e}"


@mcp.tool()
def update_note(
    path: str,
    append_content: Optional[str] = None,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    topics: Optional[list] = None,
) -> str:
    """
    기존 노트에 내용을 추가하거나 메타데이터를 수정합니다.

    Args:
        path: 볼트 내 노트 경로 (예: "projects/novalane.md")
        append_content: 본문 끝에 추가할 내용 (마크다운)
        title: 새 제목 (변경 시)
        summary: 새 요약 (변경 시)
        topics: 새 토픽 목록 (변경 시)
    """
    try:
        note = vault.update_note(
            path=path,
            append_content=append_content,
            title=title,
            summary=summary,
            topics=topics,
        )
        return f"노트 업데이트 완료: {note['path']}"
    except FileNotFoundError as e:
        return f"노트를 찾을 수 없음: {path}"
    except Exception as e:
        return f"오류: {e}"


@mcp.tool()
def set_relationship(person: str, relationship: str) -> str:
    """인물 노트의 '나와의 관계(relationship)' 라벨을 수정/설정합니다.
    get_people 에 표시되는 관계 문구(예: '아버지', '대학 친구', '직장 동료', '프론티어 구성원')를
    고칠 때 사용하세요. 이름/별칭/entity_id/people 경로 중 무엇으로든 인물을 지정할 수 있습니다.
    frontmatter를 라인 단위로 패치하므로 relations(가족 그래프) 등 다른 구조는 보존됩니다.

    Args:
        person: 인물 지정 (이름·별칭·entity_id·people 경로 중 하나)
        relationship: 새 관계 라벨 (예: "대학 친구", "직장 동료")
    """
    try:
        res = vault.set_person_relationship(person, relationship)
        return f"관계 수정 완료: {res['path']} → relationship: {res['relationship']}"
    except FileNotFoundError as e:
        return str(e)
    except Exception as e:
        return f"오류: {e}"


@mcp.tool()
def list_unenriched(folder: Optional[str] = None, limit: int = 10) -> str:
    """enrich(요약/토픽/엔티티)가 아직 없는 노트를 본문과 함께 반환합니다.
    호출 모델이 각 노트의 핵심을 읽고 summary/topics/entities 를 만들어 enrich_note 로
    써넣는 용도입니다. 한 번에 limit개씩 주므로, 처리 후 다시 호출해 다음 배치를 받으세요.

    Args:
        folder: 특정 폴더 한정 (기본: 검색 대상 전체)
        limit: 한 번에 받을 노트 수 (기본 10)
    """
    items = vault.list_unenriched(folder, limit=limit)
    if not items:
        return "enrich 대상 없음 (모두 처리됨)"
    lines = [f"enrich 대상 {len(items)}개 — 각 노트의 summary/topics/entities 를 만들어 "
             f"enrich_note(path, summary, topics, entities) 로 반영하세요.\n"]
    for it in items:
        lines.append(f"## {it['title']}\npath: {it['path']}\n{it['body']}\n")
    return "\n".join(lines)


@mcp.tool()
def enrich_note(
    path: str,
    summary: str,
    topics: Optional[list] = None,
    entities: Optional[list] = None,
    category: Optional[str] = None,
) -> str:
    """노트에 요약/토픽/엔티티를 메타데이터로 반영합니다(본문은 불변).
    list_unenriched 로 받은 노트를 읽고 만든 결과를 써넣는 용도입니다.
    enriched=true·extraction=llm-callback 로 표시되어 사람 큐레이션과 구분됩니다.
    민감/원문/격리 폴더와 민감 노트는 거부됩니다.

    Args:
        path: 노트 경로
        summary: 한두 문장 요약
        topics: 토픽 태그 목록 (가능하면 기존 토픽 택소노미에서 — list_topics 참고)
        entities: 핵심 엔티티(인물/조직/개념) 목록
        category: 분류 (선택)
    """
    try:
        res = vault.enrich_note(path, summary=summary, topics=topics,
                                entities=entities, category=category)
        if res.get("_blocked"):
            return f"차단됨: {res['reason']} ({res['path']})"
        return f"enrich 완료: {res['path']} (필드: {', '.join(res['fields'])})"
    except FileNotFoundError:
        return f"노트를 찾을 수 없음: {path}"
    except Exception as e:
        return f"오류: {e}"


@mcp.tool()
def list_topics(folder: Optional[str] = None) -> str:
    """
    볼트 전체 토픽 목록과 각 토픽의 노트 수를 반환합니다.

    Args:
        folder: 특정 폴더 한정 (기본: 전체 볼트)
    """
    topics = vault.get_all_topics(folder=folder)
    if not topics:
        return "토픽 없음"
    lines = [f"토픽 목록 ({len(topics)}개)\n"]
    for topic, count in list(topics.items())[:30]:
        lines.append(f"- {topic}: {count}개")
    return "\n".join(lines)


@mcp.tool()
def get_entity_relations(entity: str) -> str:
    """
    엔티티(인물/그룹/프로젝트/조직)의 구조화된 관계를 반환합니다.
    가족관계(father_of 등), 그룹 소속(member_of), 프로젝트 참여(works_on)를
    indexes/relations.json 그래프에서 양방향(나가는/들어오는)으로 조회합니다.
    owner 인증(Bearer 토큰) 세션이 아니면 가족·고용·그룹 소속 등 민감 관계는
    제외되고 redacted_categories 로만 표시됩니다.

    Args:
        entity: entity_id (예: "person:hong-gildong") 또는 이름 (예: "홍길동")
    """
    return _json(vault.get_relations(entity))


@mcp.tool()
def find_conversations_about(entity: str, limit: int = 20) -> str:
    """
    특정 엔티티(인물/프로젝트/조직)를 언급한 대화 노트를 찾습니다.
    답변의 근거가 되는 원문 대화를 추적할 때 사용하세요(RAG 근거 추적).

    Args:
        entity: entity_id (예: "organization:upbit") 또는 이름 (예: "Upbit")
        limit: 최대 반환 대화 수 (기본 20)
    """
    return _json(vault.find_conversations_about(entity, limit=limit))


@mcp.tool()
def get_topic_taxonomy() -> str:
    """
    토픽을 상위 카테고리(자동매매/AI·온톨로지/창작/개발 등 16개)로 묶은 택소노미를 반환합니다.
    평면 토픽 354개를 주제 축으로 탐색하거나 분류별로 노트를 좁힐 때 사용하세요.
    """
    return _json(vault.get_topic_taxonomy())


@mcp.tool()
def vault_stats() -> str:
    """
    owntology 볼트 전체 통계 (폴더별 파일 수, 경로 등)를 반환합니다.
    """
    stats = vault.get_vault_stats()
    lines = [
        f"볼트 경로: {stats['vault_path']}",
        f"전체 노트: {stats['total']}개\n",
        "폴더별:",
    ]
    for folder, cnt in stats["folder_counts"].items():
        lines.append(f"  {folder}: {cnt}개")
    if stats.get("excluded"):
        lines.append("\n기본 검색 제외(명시 folder= 조회만 가능):")
        for folder, cnt in sorted(stats["excluded"].items()):
            lines.append(f"  {folder}: {cnt}개")
    if stats.get("ops"):
        lines.append("\n품질 지표(ops):")
        lines.append(_json(stats["ops"]))
    return "\n".join(lines)


@mcp.tool()
def vault_tree(subpath: Optional[str] = None, max_depth: int = 3) -> str:
    """볼트의 폴더 구조(디렉토리 트리 + 폴더별 노트 수)를 반환합니다.
    전체 구조 파악이나 특정 폴더 하위 탐색에 사용하세요.
    민감/원문 폴더(source/kakao·accounts 등)는 blocked=true 로 표시되고 내부 구조는
    펼쳐지지 않습니다(존재/개수만 노출). 본문 조회는 여전히 차단됩니다.

    Args:
        subpath: 특정 폴더부터 보기 (예: "knowledge", "people"). 미지정 시 볼트 루트.
        max_depth: 펼칠 최대 깊이 (기본 3)
    """
    tree = vault.get_vault_tree(subpath=subpath, max_depth=max_depth)
    if tree.get("error"):
        return tree["error"]
    return _json(tree)


# ── 개인계층 후보 검토 도구 (5차 P1) ─────────────────────────

@mcp.tool()
def list_pending_candidates() -> str:
    """개인계층 후보 중 승인 대기(proposed) 목록을 반환합니다.
    각 후보의 fingerprint(fp)·유형·주장·출처를 포함하며, approve_candidate /
    reject_candidate 로 처리하세요. owner 인증 세션 전용입니다.
    """
    if (blocked := _owner_only()):
        return blocked
    import extract_personal_layer as epl
    pending = epl.list_pending()
    if not pending:
        return "승인 대기 후보 없음"
    return _json(pending)


@mcp.tool()
def approve_candidate(fp: str, title: str, summary: str, content: str = "",
                      valid_from: str = "") -> str:
    """개인계층 후보를 승인해 decisions/events/preferences 노트로 승격합니다.
    노트는 status=confirmed·extraction=user_confirmed 로 생성되고 ledger가 갱신되어
    같은 후보가 재생성되지 않습니다. owner 인증 세션 전용입니다.

    Args:
        fp: 후보 fingerprint (list_pending_candidates 참고)
        title: 노트 제목 (엔티티 이름)
        summary: 한두 문장 요약
        content: 본문 마크다운 (선택, 미지정 시 summary 사용)
        valid_from: 실제 발생일 YYYY-MM-DD — 아는 경우에만, 모르면 비워둘 것(기록일 아님)
    """
    if (blocked := _owner_only()):
        return blocked
    import extract_personal_layer as epl
    try:
        res = epl.approve_candidate(fp, title, summary, content=content,
                                    valid_from=valid_from)
        return (f"승격 완료: {res['path']} (fp {fp}) — 관계 인덱스는 다음 "
                f"enhance/rebuild 실행 시 반영됩니다.")
    except FileExistsError as e:
        return f"이미 존재하는 노트: {e}"
    except (KeyError, ValueError) as e:
        return str(e)


@mcp.tool()
def reject_candidate(fp: str, reason: str = "") -> str:
    """개인계층 후보를 거절합니다. ledger에 rejected로 남아 재생성이 방지됩니다.
    owner 인증 세션 전용입니다.

    Args:
        fp: 후보 fingerprint
        reason: 거절 사유 (선택)
    """
    if (blocked := _owner_only()):
        return blocked
    import extract_personal_layer as epl
    try:
        res = epl.reject_candidate(fp, reason=reason)
        return f"거절 처리: fp {fp}"
    except (KeyError, ValueError) as e:
        return str(e)


# ── 카카오톡 온톨로지 도구 ───────────────────────────────────

@mcp.tool()
def get_kakao_sync_status() -> str:
    """
    카카오톡 데이터의 적재 상태를 확인합니다.
    마지막 메시지 시각, 채팅방 수, 전체 메시지 수, 파싱 실패 건수를 반환합니다.
    적재가 최신인지 점검할 때 가장 먼저 호출하세요.
    """
    return _json(kakao.sync_status())


@mcp.tool()
def list_kakao_chatrooms(limit: int = 100) -> str:
    """
    온톨로지에 적재된 카카오톡 채팅방 목록을 반환합니다.
    정확한 방 이름을 모를 때 먼저 호출해 후보를 확인하세요.
    각 방의 참여자 수, 메시지 수, 최근 메시지 시각을 포함합니다 (최근순 정렬).

    Args:
        limit: 최대 반환 채팅방 수 (기본 100)
    """
    return _json(kakao.list_chatrooms(limit=limit))


@mcp.tool()
def list_kakao_members(chatroom: str) -> str:
    """
    특정 채팅방의 참여자 목록을 반환합니다.
    각 참여자의 표시 이름, canonical 인물명, 메시지 수, 최근 활동 시각을 포함합니다.
    사람 이름을 정확히 선택할 때 사용하세요.

    Args:
        chatroom: 채팅방 이름 (정확/부분 일치)
    """
    if (blocked := _owner_only()):  # 참여자 실명 노출 — owner 전용(5차 P0)
        return blocked
    return _json(kakao.list_members(chatroom))


@mcp.tool()
def get_kakao_messages(chatroom: str, sender: Optional[str] = None,
                       days: int = 7, limit: int = 100) -> str:
    """
    특정 채팅방(또는 그 안의 특정 사람)의 최근 원문 메시지를 반환합니다.
    요약 결과를 원문으로 검증할 때 사용하세요. limit이 항상 적용됩니다.

    Args:
        chatroom: 채팅방 이름
        sender: 작성자 이름/별칭 (선택)
        days: 최근 며칠치 (기본 7, 0이면 전체 기간)
        limit: 최대 반환 메시지 수 (기본 100)
    """
    if (blocked := _owner_only()):
        return blocked
    return _json(kakao.get_messages(chatroom, sender=sender, days=days, limit=limit))


@mcp.tool()
def search_kakao_messages(chatroom: Optional[str] = None, sender: Optional[str] = None,
                          keyword: Optional[str] = None, date_from: Optional[str] = None,
                          date_to: Optional[str] = None, limit: int = 100) -> str:
    """
    채팅방·작성자·키워드·기간 조건을 조합해 카카오톡 원문 메시지를 검색합니다.

    Args:
        chatroom: 채팅방 이름 (선택, 미지정 시 전체 방)
        sender: 작성자 이름/별칭 (선택)
        keyword: 본문 포함 키워드 (선택)
        date_from: 시작일 YYYY-MM-DD (선택, 포함)
        date_to: 종료일 YYYY-MM-DD (선택, 포함)
        limit: 최대 반환 메시지 수 (기본 100)
    """
    if (blocked := _owner_only()):
        return blocked
    return _json(kakao.search_messages(chatroom=chatroom, sender=sender, keyword=keyword,
                                       date_from=date_from, date_to=date_to, limit=limit))


@mcp.tool()
def resolve_kakao_person_alias(name: str, chatroom: Optional[str] = None) -> str:
    """
    '재은이형', '팀장님' 같은 호칭·별칭을 실제 인물 엔티티에 연결합니다.
    people 노트의 별칭과 채팅방 참여자명을 매칭하며 confidence를 함께 반환합니다.

    Args:
        name: 입력 호칭/별칭/이름
        chatroom: 맥락 채팅방 (선택, 동명이인 구분에 도움)
    """
    if (blocked := _owner_only()):  # 별칭→실명 신원 해석 — owner 전용(5차 P0)
        return blocked
    return _json(kakao.resolve_person_alias(name, chatroom=chatroom))


@mcp.tool()
def summarize_kakao_person(chatroom: str, sender: str, days: int = 30) -> str:
    """
    특정 인물이 최근 완료·진행·요청·보고·예정한 업무를 근거 메시지와 함께 정리합니다.
    키워드 휴리스틱 분류 + 원문 근거(evidence_message_ids)를 제공합니다.
    최종 요약 문장은 반환된 근거를 검토해 호출 측에서 작성하세요.

    Args:
        chatroom: 채팅방 이름
        sender: 대상 인물 이름/별칭
        days: 분석 기간 (기본 30일)
    """
    if (blocked := _owner_only()):  # 특정 인물 활동 프로파일링 — owner 전용(5차 P0)
        return blocked
    return _json(kakao.summarize_person(chatroom, sender, days=days))


@mcp.tool()
def find_kakao_projects(chatroom: Optional[str] = None, sender: Optional[str] = None,
                        days: Optional[int] = None) -> str:
    """
    대화에서 언급된 프로젝트/시스템명을 추출하고 기존 projects/ 엔티티와 연결합니다.
    관련 인물과 근거 메시지를 함께 반환합니다.

    Args:
        chatroom: 채팅방 이름 (선택)
        sender: 작성자 (선택)
        days: 최근 며칠 (선택, 미지정 시 전체)
    """
    return _json(kakao.find_projects(chatroom=chatroom, sender=sender, days=days))


@mcp.tool()
def get_kakao_upcoming_tasks(chatroom: Optional[str] = None, assignee: Optional[str] = None,
                             days: int = 30) -> str:
    """
    대화에서 앞으로 해야 할 일·담당자·예정일/마감 단서·근거 메시지를 추출합니다.
    키워드/날짜 휴리스틱이며 각 항목에 confidence가 붙습니다.

    Args:
        chatroom: 채팅방 이름 (선택)
        assignee: 담당자 이름/별칭 (선택)
        days: 최근 며칠 (기본 30)
    """
    return _json(kakao.upcoming_tasks(chatroom=chatroom, assignee=assignee, days=days))


# ── 진입점 ───────────────────────────────────────────────────

class BearerAuthMiddleware:
    """MCP http 요청의 Authorization: Bearer 토큰을 검증해 owner 여부를 표시(순수 ASGI —
    BaseHTTPMiddleware는 ContextVar 전파가 끊겨 사용 불가).

    - 헤더 없음 → public 티어(비민감만). 기존 동작 유지(하위호환).
    - 유효 토큰 → owner 티어(민감 큐레이션 노트·카카오 원문 도구 허용).
    - 헤더는 있으나 토큰 불일치 → 401.
    토큰 미설정(빈 값) 서버는 owner 승격 자체를 하지 않는다."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization", b"").decode()
        provided = raw[7:].strip() if raw[:7].lower() == "bearer " else ""
        valid = bool(self.token) and len(self.token) >= 16 and provided == self.token
        if provided and not valid:
            from starlette.responses import JSONResponse
            audit.log("mcp", "auth", None, {"blocked": True},
                      {"owner": False, "auth_event": "invalid_token"})
            return await JSONResponse({"detail": "invalid bearer token"},
                                      status_code=401)(scope, receive, send)
        tok = security.set_owner(valid)
        if valid:
            audit.log("mcp", "auth", None, None,
                      {"owner": True, "auth_event": "authenticated_owner"})
        try:
            await self.app(scope, receive, send)
        finally:
            security.reset_owner(tok)


if __name__ == "__main__":
    # 기본은 로컬(stdio) — Claude Desktop·Claude Code·Codex·Gemini가 직접 실행/연결.
    # 클라우드 노출 시 MCP_TRANSPORT=streamable-http (+ OWNTOLOGY_TOKEN·OWNTOLOGY_PUBLIC_HOST).
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        # 로컬 stdio = 본인이 자기 Mac에서 직접 실행 → owner 티어(카카오·인물 등 전체 접근).
        # (http는 Bearer 토큰을 제시한 요청만 owner로 승격 — 아래 미들웨어가 담당.)
        security.set_owner(True)
    else:
        print(f"owntology MCP server starting on {_host}:{_port} ({transport})", file=sys.stderr)
    vault.prewarm_cache()
    if transport in ("streamable-http", "sse"):
        import uvicorn
        app = (mcp.streamable_http_app() if transport == "streamable-http"
               else mcp.sse_app())
        app.add_middleware(BearerAuthMiddleware, token=TOKEN)
        uvicorn.run(app, host=_host, port=_port, log_level="info")
    else:
        mcp.run(transport=transport)
