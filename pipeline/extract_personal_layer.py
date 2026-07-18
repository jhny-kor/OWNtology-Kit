#!/usr/bin/env python3
"""대화→개인계층 추출 파이프라인 (로드맵 item 2).

conversations/ 큐레이션 노트의 LLM summary/title에서 decision·event·preference 후보를
휴리스틱+근거로 추출해 ontology/personal-layer-candidates-<date>.md 리포트를 생성한다.
relation-candidates와 동일한 거버넌스: 전부 status: proposed, 사용자 승인 시에만 실제
decisions/events/preferences 노트로 승격(자동 확정 금지).

증분·중복 방지(위원회 4차 P2): 후보마다 fingerprint(source+type+정규화 주장)를 부여해
indexes/personal_layer_ledger.json 에 기록한다. ledger에 있는 후보(상태 무관 —
proposed/confirmed/rejected/superseded)는 다음 실행에서 재생성하지 않는다.
상태 변경은 ledger JSON의 해당 fingerprint 항목 status 를 직접 수정하면 된다.
기본 실행은 마지막 --apply 이후 변경된 대화만 스캔한다(--full 로 전체 재스캔).

실행:
  OWNTOLOGY_VAULT=~/Documents/owntology python3 extract_personal_layer.py            # dry-run
  OWNTOLOGY_VAULT=~/Documents/owntology python3 extract_personal_layer.py --apply    # 리포트 작성
  옵션: --limit N (범주별 상한, 기본 25), --days N (최근 N일만), --full (증분 무시 전체 스캔)
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import load as _load_cfg, vault_path as _vp  # noqa: E402
_vp()  # OWNTOLOGY_VAULT 환경변수 설정 (vault.py가 읽음)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kitlib"))
import vault  # noqa: E402
import security  # noqa: E402

# (정규식, confidence) — 강한 신호일수록 높은 confidence. 고정밀 우선(위원회 경고: 과잉흡수 방지).
DECISION = [
    (re.compile(r"하기로\s*(했|결정|정)"), 0.55),
    (re.compile(r"결정(했|하였|함|됨)"), 0.5),
    (re.compile(r"(도입|채택|전환)(하기로|했|함|하기)"), 0.5),
    (re.compile(r"(방침|결론)(으로|을|이)"), 0.4),
    (re.compile(r"(로|으로)\s*(정했|정하기로|가기로)"), 0.45),
]
EVENT = [
    (re.compile(r"(출시|발매|배포|릴리스|런칭|오픈)\s*(완료|했|함|됨|함\.)"), 0.55),
    (re.compile(r"(완료|마무리|끝냈|성공)(했|함|됨)"), 0.4),
    (re.compile(r"(연동|구축|출시)\s*완료"), 0.5),
]
PREFERENCE = [
    (re.compile(r"(선호|즐겨|주로|항상)\s*\S*\s*(한다|하는|쓴다|사용|이용)"), 0.45),
    (re.compile(r"기본(값|으로 (사용|설정)|\s*스택)"), 0.4),
    (re.compile(r"(로|으로)\s*통일"), 0.45),
]  # 주의: 바 '늘'은 '오늘'에 오탐 → 사용 금지
CATS = {"decision": DECISION, "event": EVENT, "preference": PREFERENCE}

# ── 카카오 본인 발화 패스 (위원회 P1: 생활 사실 — 소비·예약·건강·선호) ──
# 대상은 본인 메시지만(타인 발언을 본인 사실로 오귀속 방지). 결과 리포트는
# sensitivity: sensitive(생활 내용 포함 → 기본 검색 비노출, owner만 열람).
SELF_AUTHORS = {"나", "(me)"} | (
    {(_load_cfg().get("me", {}).get("kakao_nickname") or "").strip()} - {""})
KAKAO_CATS = {
    "decision": [
        (re.compile(r"(하기로|사기로|가기로|안\s?하기로)\s*(했|함)"), 0.5),
        (re.compile(r"결정(했|함)"), 0.5),
    ],
    "event": [
        (re.compile(r"(예약|계약|구매|주문|등록|접수|납부|환불)\s*(했|함|완료)"), 0.5),
        (re.compile(r"(다녀왔|다녀옴|갔다\s?왔)"), 0.45),
        (re.compile(r"(이사|입주|퇴사|입사|합격|당첨)(했|함|됨)"), 0.55),
        (re.compile(r"(샀다|샀어|샀음|질렀)"), 0.45),
        (re.compile(r"(병원|진료|검진)\s*(갔|다녀|예약)"), 0.5),
    ],
    "preference": [
        (re.compile(r"(제일|가장)\s*(좋|맛있|편했)"), 0.45),
        (re.compile(r"(좋아해|좋아함|선호)"), 0.4),
        # 주의: '별로/싫어'는 조사 '~별로'(본부별로)·일시 기분과 구분 불가 → 미사용
    ],
}
KAKAO_LIMITS = {"decision": 30, "event": 50, "preference": 20}


def extract_kakao(days: int, ledger: dict | None = None):
    """최근 N일 카카오 본인 발화에서 생활 사실 후보 추출. 근거는 마스킹 후 120자."""
    import kakao
    seen = set()
    known = set((ledger or {}).get("candidates", {}))
    cands = {c: [] for c in KAKAO_CATS}
    for room, m in kakao._iter_filtered(None, None, days):
        if m.get("author") not in SELF_AUTHORS:
            continue
        body = (m.get("body") or "").strip()
        if not (5 <= len(body) <= 300):
            continue
        # 공유 링크/뉴스 인용(제3자 서술)과 의문문(타인에게 묻는 말)은 본인 사실 아님
        if "http" in body or "?" in body:
            continue
        key = _norm(body)[:40]
        if key in seen:
            continue
        room_src = f"kakao:{room['name']}"
        for cat, pats in KAKAO_CATS.items():
            hit = _best_signal(body, pats)
            if not hit:
                continue
            fp = _fingerprint(room_src, cat, body)
            if fp in known:
                break  # 이전 실행에서 이미 제안됨(P2)
            seen.add(key)
            cands[cat].append({
                "conf": hit[1],
                "date": (m.get("dt").strftime("%Y-%m-%d") if m.get("dt") else "?"),
                "room": room["name"],
                "room_src": room_src,
                "snippet": security.redact(body)[:120],
                "fp": fp,
            })
            break  # 메시지당 1범주
    for cat in cands:
        cands[cat].sort(key=lambda x: (-x["conf"], x["date"]), reverse=False)
        cands[cat] = sorted(cands[cat], key=lambda x: -x["conf"])[:KAKAO_LIMITS[cat]]
    return cands


def render_kakao(cands, days: int) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    total = sum(len(v) for v in cands.values())
    lines = [
        "---",
        "type: note",
        f'title: "카카오 개인계층 후보 (최근 {days}일)"',
        f"date: {today}",
        'summary: "카카오 본인 발화에서 추출한 decision/event/preference 후보. 전부 proposed — 사용자 승인 시에만 승격."',
        'topics: ["owntology", "개인계층", "카카오"]',
        "sensitivity: sensitive",
        "---",
        "",
        f"# 카카오 개인계층 후보 (본인 발화, 최근 {days}일, status: proposed)",
        "",
        f"생성: {today} · 후보 {total}건. **자동 확정 금지** — 검토 후 선택 승격.",
        "본인 메시지만 대상, 근거는 시크릿 마스킹 적용. sensitivity=sensitive(owner만 열람).",
        "",
    ]
    label = {"decision": "결정", "event": "이벤트(생활)", "preference": "선호"}
    for cat in ("decision", "event", "preference"):
        rows = cands[cat]
        lines += [f"## {label[cat]} — {len(rows)}건", ""]
        if not rows:
            lines.append("_후보 없음_\n")
            continue
        lines += ["| conf | 날짜 | 방 | 발화 | fp |", "|---:|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r['conf']} | {r['date']} | {r['room'][:12]} | {r['snippet'].replace('|', chr(92)+'|')} | `{r['fp']}` |")
        lines.append("")
    return "\n".join(lines)

_DATE_RE = re.compile(r"(20\d{2})[-.](\d{1,2})[-.](\d{1,2})")


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


# ── 후보 ledger (P2: fingerprint 기반 중복 방지 + 증분 처리) ──
def _ledger_path() -> Path:
    return vault.VAULT_PATH / "indexes" / "personal_layer_ledger.json"


def _fingerprint(source: str, cat: str, claim: str) -> str:
    """source 경로 + 후보 유형 + 정규화 주장 → 고유 fingerprint."""
    return hashlib.sha1(f"{source}|{cat}|{_norm(claim)[:60]}".encode()).hexdigest()[:16]


def _load_ledger() -> dict:
    p = _ledger_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_run": None, "candidates": {}}


def _save_ledger(led: dict) -> None:
    p = _ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(led, ensure_ascii=False, indent=1), encoding="utf-8")


# ── 후보 검토 워크플로 (5차 P1: MCP 도구가 호출) ─────────────
def list_pending() -> list:
    """승인 대기(proposed) 후보 목록."""
    led = _load_ledger()
    return [{"fp": fp, **c} for fp, c in led["candidates"].items()
            if c.get("status") == "proposed"]


_PROMOTE_FOLDERS = {"decision": "decisions", "event": "events", "preference": "preferences"}


def approve_candidate(fp: str, title: str, summary: str, content: str = "",
                      valid_from: str = "") -> dict:
    """후보를 승인해 해당 계층 노트를 생성하고 ledger를 confirmed로 갱신한다.
    valid_from은 실제 발생일을 아는 경우에만 — 모르면 빈 값(unknown, P2 규칙)."""
    led = _load_ledger()
    cand = led["candidates"].get(fp)
    if not cand:
        raise KeyError(f"후보 없음: {fp}")
    if cand.get("status") != "proposed":
        raise ValueError(f"이미 처리된 후보({cand['status']}): {fp}")
    cat = cand.get("type", "")
    folder = _PROMOTE_FOLDERS.get(cat)
    if not folder:
        raise ValueError(f"알 수 없는 후보 유형: {cat}")
    slug = vault._slugify(title)
    path = f"{folder}/{slug}.md"
    today = datetime.now().strftime("%Y-%m-%d")
    text = "\n".join([
        "---",
        f"type: {cat}",
        f"entity_id: {cat}:{slug}",
        "canonical: true",
        f'name: "{title}"',
        f"tags: [{cat}]",
        "sensitivity: private",
        f'source_path: "{path}"',
        f"valid_from: {valid_from}",
        "valid_to: ",
        "confidence: 0.85",
        "extraction: user_confirmed",
        f'sources: ["{cand.get("source", "")}"]',
        "status: confirmed",
        f"verified_at: {today}",
        f'summary: "{summary}"',
        "---",
        "",
        f"# {title}",
        "",
        content.strip() or summary,
        "",
        f"> 개인계층 후보(fp {fp})에서 사용자 승인으로 승격({today}).",
        "",
    ])
    vault.write_raw_md(path, text)  # 이미 존재하면 FileExistsError
    cand.update(status="confirmed", promoted_to=path, reviewed_at=today)
    _save_ledger(led)
    return {"fp": fp, "path": path, "status": "confirmed"}


def reject_candidate(fp: str, reason: str = "") -> dict:
    """후보를 거절 처리한다. ledger에 남아 재생성이 방지된다."""
    led = _load_ledger()
    cand = led["candidates"].get(fp)
    if not cand:
        raise KeyError(f"후보 없음: {fp}")
    if cand.get("status") != "proposed":
        raise ValueError(f"이미 처리된 후보({cand['status']}): {fp}")
    cand.update(status="rejected", reviewed_at=datetime.now().strftime("%Y-%m-%d"))
    if reason:
        cand["reject_reason"] = reason
    _save_ledger(led)
    return {"fp": fp, "status": "rejected"}


def _record_candidates(led: dict, cands: dict, prefix: str = "") -> int:
    """새 후보를 ledger에 proposed로 기록. 반환: 신규 기록 수."""
    new = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for cat, rows in cands.items():
        for r in rows:
            fp = r["fp"]
            if fp in led["candidates"]:
                continue
            led["candidates"][fp] = {
                "status": "proposed", "type": cat, "first_seen": today,
                "source": r.get("path") or r.get("room_src") or "",
                "claim": (r.get("title") or r.get("snippet") or "")[:80],
            }
            new += 1
    return new


def _existing_keys(folder: str) -> set:
    """기존 계층 노트의 name/title/summary에서 중복판정용 정규화 토큰 집합."""
    keys = set()
    for f in (vault.VAULT_PATH / folder).glob("*.md"):
        fm, _ = vault._parse_frontmatter(f.read_text(encoding="utf-8", errors="ignore"))
        for k in (fm.get("name"), fm.get("title"), fm.get("summary")):
            if k:
                keys.add(_norm(str(k)))
    return keys


def _sourced_paths() -> set:
    """이미 decisions/events/preferences 노트가 sources로 인용한 대화 경로 집합.
    승격 완료된 후보가 다음 추출에서 재추천되는 것을 막는다(경로 기반 dedup)."""
    paths = set()
    for folder in ("decisions", "events", "preferences"):
        base = vault.VAULT_PATH / folder
        if not base.exists():
            continue
        for f in base.glob("*.md"):
            fm, _ = vault._parse_frontmatter(f.read_text(encoding="utf-8", errors="ignore"))
            for s in (fm.get("sources") or []):
                if isinstance(s, str) and s.startswith("conversations/"):
                    paths.add(s)
    return paths


def _best_signal(text: str, patterns):
    hit = None
    for pat, conf in patterns:
        m = pat.search(text)
        if m and (hit is None or conf > hit[1]):
            hit = (m, conf)
    return hit


def extract(limit: int, days: int | None, ledger: dict | None = None,
            since_ts: float | None = None):
    cutoff = None
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    existing = {c: _existing_keys(c + "s") for c in CATS}  # decisions/events/preferences
    sourced = _sourced_paths()  # 이미 노트로 승격된 대화 경로
    known = set((ledger or {}).get("candidates", {}))  # 이미 제안·처리된 fingerprint
    seen = {c: set() for c in CATS}  # 리포트 내 중복 억제
    cands = {c: [] for c in CATS}

    for f in vault._iter_notes("conversations"):
        if since_ts:
            try:
                if f.stat().st_mtime < since_ts:
                    continue  # 증분: 마지막 --apply 이후 변경된 대화만
            except OSError:
                continue
        note = vault._cached_note(f)
        if not note or note.get("_blocked"):
            continue
        if security.is_sensitivity_blocked(note.get("sensitivity"), honor_owner=False):
            continue
        if note["path"] in sourced:
            continue  # 이미 노트로 승격된 대화 → 재추천 안 함
        date = str(note.get("date", ""))
        if cutoff and date and date < cutoff:
            continue
        title = note.get("title", "")
        summary = note.get("summary", "")
        # summary(LLM 요약) 필수 — 제목만 있는 codex 태스크 로그는 노이즈라 제외.
        if len(summary) < 20:
            continue
        text = f"{title}. {summary}"
        for cat, pats in CATS.items():
            # 신호는 요약 본문에서만 탐지(제목의 파일명 토큰 오탐 방지)
            hit = _best_signal(summary, pats)
            if not hit:
                continue
            m, conf = hit
            key = _norm(title)[:40]
            if key in seen[cat] or key in existing[cat]:
                continue  # 이미 있거나 리포트 내 중복
            fp = _fingerprint(note["path"], cat, title or summary)
            if fp in known:
                continue  # 이전 실행에서 이미 제안됨(P2) — 상태 무관 재생성 금지
            # event는 날짜 근거가 있으면 가점, 없으면 감점
            if cat == "event":
                dm = _DATE_RE.search(text) or (date and _DATE_RE.search(date.replace("-", "-")))
                conf = conf + 0.05 if (dm or date) else conf - 0.1
            # 근거 스니펫: 매칭 주변 컨텍스트(summary 기준 오프셋)
            s, e = max(0, m.start() - 30), min(len(summary), m.end() + 30)
            snippet = summary[s:e].strip().replace("\n", " ")
            seen[cat].add(key)
            cands[cat].append({
                "title": title[:60] or "(제목 없음)",
                "conf": round(conf, 2),
                "date": date or "?",
                "path": note["path"],
                "snippet": snippet,
                "fp": fp,
            })

    for cat in cands:
        cands[cat].sort(key=lambda x: x["conf"], reverse=True)
        cands[cat] = cands[cat][:limit]
    return cands


def render(cands) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    total = sum(len(v) for v in cands.values())
    lines = [
        f"# 개인계층 후보 리포트 (conversations → decision/event/preference, status: proposed)",
        "",
        f"생성: {today} · 후보 {total}건. 대화 summary/title 휴리스틱 자동 추출.",
        "**자동 확정 금지** — 사용자가 검토 후 선택 항목만 decisions/events/preferences 노트로 승격할 것.",
        "(relation-candidates와 동일한 거버넌스. 근거 경로로 원문 확인 가능.)",
        "",
    ]
    label = {"decision": "결정(decision)", "event": "이벤트(event)", "preference": "선호(preference)"}
    for cat in ("decision", "event", "preference"):
        rows = cands[cat]
        lines.append(f"## {label[cat]} — {len(rows)}건")
        lines.append("")
        if not rows:
            lines.append("_후보 없음_\n")
            continue
        lines.append("| conf | 날짜 | 제목 | 근거 스니펫 | 출처 | fp |")
        lines.append("|---:|---|---|---|---|---|")
        for r in rows:
            snip = r["snippet"].replace("|", "\\|")
            title = r["title"].replace("|", "\\|")
            lines.append(f"| {r['conf']} | {r['date']} | {title} | …{snip}… | `{r['path']}` | `{r['fp']}` |")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="리포트 파일 작성(기본: dry-run 미리보기)")
    ap.add_argument("--limit", type=int, default=25, help="범주별 상한(기본 25)")
    ap.add_argument("--days", type=int, default=None, help="최근 N일 대화만(기본 전체)")
    ap.add_argument("--full", action="store_true", help="증분 무시, 전체 대화 재스캔")
    ap.add_argument("--kakao-days", type=int, default=None,
                    help="카카오 본인 발화 패스 활성(최근 N일) — 별도 sensitive 리포트 생성")
    args = ap.parse_args()

    ledger = _load_ledger()
    since_ts = None if args.full else ledger.get("last_run")

    cands = extract(args.limit, args.days, ledger=ledger, since_ts=since_ts)
    report = render(cands)
    total = sum(len(v) for v in cands.values())
    if args.apply:
        if total:
            out = vault.VAULT_PATH / "ontology" / f"personal-layer-candidates-{datetime.now():%Y%m%d}.md"
            out.write_text(report, encoding="utf-8")
            print(f"작성: {out} ({total}건)")
        else:
            print("신규 후보 0건 — 리포트 미작성(기존 리포트 보존)")
    else:
        print(report)
        print(f"\n[dry-run] {total}건. --apply로 리포트 작성.", file=sys.stderr)

    ktotal = 0
    kc = None
    if args.kakao_days:
        kc = extract_kakao(args.kakao_days, ledger=ledger)
        kr = render_kakao(kc, args.kakao_days)
        ktotal = sum(len(v) for v in kc.values())
        if args.apply:
            if ktotal:
                kout = vault.VAULT_PATH / "ontology" / f"personal-layer-kakao-{datetime.now():%Y%m%d}.md"
                kout.write_text(kr, encoding="utf-8")
                print(f"작성: {kout} ({ktotal}건, sensitive)")
            else:
                print("신규 카카오 후보 0건 — 리포트 미작성")
        else:
            print(kr)
            print(f"\n[dry-run kakao] {ktotal}건.", file=sys.stderr)

    if args.apply:
        # 제안된 후보를 ledger에 proposed로 기록 → 다음 실행부터 재생성 안 됨(P2)
        new = _record_candidates(ledger, cands)
        if kc:
            new += _record_candidates(ledger, kc)
        import time as _time
        ledger["last_run"] = _time.time()
        _save_ledger(ledger)
        print(f"ledger: 신규 {new}건 proposed 기록, 총 {len(ledger['candidates'])}건 "
              f"→ {_ledger_path()}")


if __name__ == "__main__":
    main()
