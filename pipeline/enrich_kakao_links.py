#!/usr/bin/env python3
"""Enrich KakaoTalk self-chat links into the ontology.

Pipeline (runs after kakao_self_backup.py / sync.py):
  1. Scan source/kakao/kmsg-chat-*.json for URLs in messages.
  2. For each NEW url (state-tracked), fetch the page and extract — with no
     external API or key — a title and an extractive summary
     (og:description -> meta description -> first readable paragraphs).
  3. Classify by domain into a category.
  4. Write/refresh ontology/kakao-<chat>-links-enriched.json (catalog) and a
     per-link vault note under knowledge/links/ (type: link) for Obsidian graph.

Usage:
  python3 pipeline/enrich_kakao_links.py                 # self-chat (config 닉네임)
  python3 pipeline/enrich_kakao_links.py --chat <방이름> --limit 50
  python3 ontology/enrich_kakao_links.py --all           # every kakao chat
  python3 ontology/enrich_kakao_links.py --refetch       # ignore state, redo all
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()
KAKAO_SOURCE = VAULT / "source" / "kakao"
ONTOLOGY = VAULT / "ontology"
STATE_FILE = ONTOLOGY / ".enrich_kakao_links_state.json"
LOG_FILE = VAULT / ".sync.log"

# self-chat ("나와의 채팅")은 소유자 닉네임으로 표시된다 — config에서 읽는다.
from kitlib.config import load as _load_cfg
DEFAULT_CHAT = (_load_cfg().get("me", {}).get("kakao_nickname") or "나").strip()
FETCH_TIMEOUT = 10             # socket timeout per operation
HARD_TIMEOUT = 15             # absolute wall-clock cap per URL (kills streaming/trickle hangs)
FETCH_DELAY = 0.7             # polite delay between requests
MAX_HTML_BYTES = 300_000       # og/meta/title live in <head>; cap read for speed
SUMMARY_MAX = 600              # chars

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

URL_RE = re.compile(r"https?://[^\s)\"'<>]+")

# domain -> category (english slugs, matching existing enriched catalog)
DOMAIN_CATEGORY = {
    "youtube.com": "youtube", "youtu.be": "youtube",
    "github.com": "code", "gitlab.com": "code",
    "arxiv.org": "paper",
    "notion.so": "notion",
    "drive.google.com": "google", "docs.google.com": "google", "google.com": "google",
    "x.com": "twitter", "twitter.com": "twitter",
    "threads.net": "threads",
    "openai.com": "ai", "claude.ai": "ai", "claude.com": "ai", "anthropic.com": "ai",
    "news.hada.io": "news",
}


def log(msg: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] [kakao-links] {msg}"
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


# ---------------------------------------------------------------- state
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- extract links
def category_for(domain: str) -> str:
    d = domain.lower()
    if d.startswith("www."):
        d = d[4:]
    for key, cat in DOMAIN_CATEGORY.items():
        if d == key or d.endswith("." + key):
            return cat
    # heuristic fallbacks
    if any(seg in d for seg in (".github.io", "pages.dev", "vercel.app", "netlify.app")):
        return "site"
    return "other"


def _safe_domain(url: str) -> str:
    """urlparse가 잘못된 URL('[' 등)에 ValueError(Invalid IPv6 URL)를 던져 전체 수집을
    크래시시키는 것을 막는다. 실패 시 정규식으로 호스트만 best-effort 추출."""
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        m = re.match(r"https?://([^/?#]+)", url)
        return m.group(1).lower() if m else ""


def collect_links(chat_filter: str | None) -> dict[str, list[dict]]:
    """Return {chat_name: [link dicts]} from kmsg JSON snapshots, deduped by URL.

    Keeps the earliest (chat, date, author) seen for each URL within a chat.
    """
    # CLI 한글 인자는 macOS에서 NFD로 들어와 NFC인 chat 필드와 불일치(매칭 0) → 정규화.
    chat_filter = nfc(chat_filter) if chat_filter else chat_filter
    by_chat: dict[str, dict[str, dict]] = {}
    # kmsg-chat-*(구 kmsg) + kmsg-katok-*(katok 주력) 둘 다 스캔. katok이 주력이 된 뒤
    # kmsg-chat-* 만 보면 카톡 링크 대부분(katok export)을 놓친다.
    for f in sorted(KAKAO_SOURCE.glob("kmsg-*.json")):
        try:
            text = f.read_text(encoding="utf-8").strip()
            if not text.startswith("{"):
                continue
            data = json.loads(text)
        except Exception:
            continue
        chat = nfc(data.get("chat") or data.get("chat_id") or f.stem)
        if chat_filter and chat != chat_filter:
            continue
        fetched = (data.get("fetched_at") or "")[:10]
        bucket = by_chat.setdefault(chat, {})
        for m in data.get("messages", []):
            body = m.get("body") or m.get("text") or ""
            when = (m.get("time_raw_with_date") or fetched or "")[:10]
            author = m.get("author") or ""
            for raw in URL_RE.findall(body):
                url = raw.rstrip(".,)]}'\"")
                if url in bucket:
                    # keep earliest date
                    if when and when < bucket[url]["date"]:
                        bucket[url]["date"] = when
                    continue
                bucket[url] = {
                    "date": when or fetched, "author": author, "url": url,
                    "domain": _safe_domain(url),
                }
    return {c: list(v.values()) for c, v in by_chat.items()}


# ---------------------------------------------------------------- fetch + extract
_TAG_RE = re.compile(r"<[^>]+>")
# tempered greedy(선형): 닫는 태그 없는 깨진 HTML에서도 O(n) — `.*?</\1>`는 ReDoS였음.
_SCRIPT_RE = re.compile(r"<(script|style|noscript)\b[^>]*>(?:[^<]|<(?!/\1\b))*</\1\s*>", re.I | re.S)
# 선형: 메타 태그를 경계('>')+길이상한으로 먼저 매칭한 뒤 속성을 작은 태그 문자열에서 파싱.
# (구 `<meta\s+[^>]*?...content...(.*?)`는 '>' 없는 입력에서 finditer×위치 = O(n²) ReDoS였음)
_META_TAG_RE = re.compile(r"<meta\b[^>]{0,3000}>", re.I)
_META_NAME_RE = re.compile(
    r'(?:name|property)\s*=\s*["\']?'
    r'(og:title|og:description|twitter:description|description)\b', re.I)
_META_CONTENT_RE = re.compile(r'content\s*=\s*["\']([^"\']*)', re.I)
_TITLE_RE = re.compile(r"<title[^>]*>([^<]{0,1000})", re.I | re.S)  # 선형·상한 (제목엔 '<' 없음)
_P_RE = re.compile(r"<p\b[^>]*>((?:[^<]|<(?!/p\s*>))*)</p\s*>", re.I | re.S)


def _clean(s: str) -> str:
    s = html.unescape(_TAG_RE.sub(" ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _decode(raw: bytes, ctype: str) -> str:
    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    if m:
        charset = m.group(1)
    else:
        m2 = re.search(rb'charset=["\']?([\w-]+)', raw[:2000], re.I)
        if m2:
            charset = m2.group(1).decode("ascii", "ignore")
    try:
        return raw.decode(charset, "replace")
    except (LookupError, TypeError):
        return raw.decode("utf-8", "replace")


def fetch(url: str) -> dict:
    """Fetch with an absolute wall-clock cap so no URL (e.g. a streaming/radio
    endpoint) can stall the run. Runs the network work in a daemon thread."""
    box: dict = {}

    def _work():
        box["res"] = _fetch_inner(url)

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(HARD_TIMEOUT)
    if t.is_alive():
        return {"title": None, "summary": None, "status": "fetch_failed:hard_timeout"}
    return box.get("res") or {"title": None, "summary": None, "status": "fetch_failed:empty"}


def _fetch_inner(url: str) -> dict:
    """Return {title, summary, status}. status: ok | fetch_failed | non_html."""
    req = Request(url, headers={"User-Agent": UA, "Accept-Language": "ko,en;q=0.8",
                                "Accept-Encoding": "gzip"})
    try:
        with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype.lower() and "text" not in ctype.lower():
                return {"title": None, "summary": None, "status": "non_html"}
            raw = resp.read(MAX_HTML_BYTES)
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
    except Exception as e:
        return {"title": None, "summary": None, "status": f"fetch_failed:{type(e).__name__}"}

    doc = _decode(raw, ctype)[:300_000]  # 파싱 입력 상한(ReDoS·메모리 방어; 메타/제목은 앞부분)
    metas: dict[str, str] = {}
    for tag in _META_TAG_RE.finditer(doc):
        s = tag.group(0)
        nm = _META_NAME_RE.search(s)
        ct = _META_CONTENT_RE.search(s)
        if nm and ct:
            metas.setdefault(nm.group(1).lower(), _clean(ct.group(1)))

    tm = _TITLE_RE.search(doc)
    title = metas.get("og:title") or (_clean(tm.group(1)) if tm else None)

    summary = (metas.get("og:description") or metas.get("description")
               or metas.get("twitter:description"))
    if not summary:
        # extractive fallback: first substantial paragraphs
        body = _SCRIPT_RE.sub(" ", doc)
        chunks = []
        for pm in _P_RE.finditer(body):
            t = _clean(pm.group(1))
            if len(t) >= 40:
                chunks.append(t)
            if sum(len(c) for c in chunks) >= SUMMARY_MAX:
                break
        summary = " ".join(chunks) if chunks else None

    if summary and len(summary) > SUMMARY_MAX:
        summary = summary[:SUMMARY_MAX].rsplit(" ", 1)[0] + "…"
    return {"title": title, "summary": summary, "status": "ok"}


# ---------------------------------------------------------------- vault note
def _slug(s: str, maxlen: int = 60) -> str:
    s = nfc(s).strip()
    s = re.sub(r"[\\/:*?\"<>|#\[\]]", "", s)
    s = re.sub(r"\s+", "_", s)
    return s[:maxlen].strip("_") or "link"


# ---------------------------------------------------------------- catalog
def write_catalog(chat: str, links: list[dict]) -> Path:
    ONTOLOGY.mkdir(parents=True, exist_ok=True)
    # chat에 '/'·':' 등 경로문자가 들어올 수 있다(1:1 방 닉네임 "이안/youjin" 등) → 파일명 sanitize.
    out = ONTOLOGY / f"kakao-{_slug(chat, 80)}-links-enriched.json"
    ordered = sorted(links, key=lambda l: (l.get("date") or "", l["url"]))
    obj = {
        "chat": chat,
        "source_file": "kmsg-chat-*.json (merged)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "link_count": len(ordered),
        "links": ordered,
    }
    out.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# 도메인 화이트리스트: 고가치·fetch가능 도메인만 enrich (cafe.naver=73% 로그인벽/정크 제외).
WL_SUFFIX = ("youtube.com", "youtu.be", "github.com", "arxiv.org", "huggingface.co",
             "threads.com", "x.com", "twitter.com", "brunch.co.kr", "velog.io",
             "medium.com", "substack.com", "chatgpt.com", "hada.io", "notion.site",
             "tistory.com", "stibee.com", "maven.com")
WL_EXACT = {"n.news.naver.com", "news.naver.com", "m.news.naver.com",
            "m.blog.naver.com", "blog.naver.com", "v.daum.net", "outstanding.kr"}


def domain_allowed(domain: str, whitelist: tuple | None) -> bool:
    """whitelist=None이면 전부 허용. 아니면 WL_EXACT 정확일치 또는 suffix 매칭."""
    if whitelist is None:
        return True
    return (domain in WL_EXACT
            or any(domain == s or domain.endswith("." + s) for s in whitelist))


def main() -> int:
    ap = argparse.ArgumentParser(description="Enrich KakaoTalk links into the ontology")
    ap.add_argument("--chat", default=DEFAULT_CHAT, help='chat name (default: self-chat)')
    ap.add_argument("--all", action="store_true", help="process every kakao chat")
    ap.add_argument("--limit", type=int, default=0, help="max NEW links to fetch this run (0 = all)")
    ap.add_argument("--refetch", action="store_true", help="ignore state, re-fetch everything")
    ap.add_argument("--domains", default="", help="쉼표구분 도메인 화이트리스트(suffix매칭). "
                    "미지정=기본 고가치 목록, 'all'=필터 끔(전 도메인)")
    args = ap.parse_args()

    chat_filter = None if args.all else args.chat
    if args.domains.strip().lower() == "all":
        whitelist = None
    elif args.domains.strip():
        whitelist = tuple(d.strip() for d in args.domains.split(",") if d.strip())
    else:
        whitelist = WL_SUFFIX
    by_chat = collect_links(chat_filter)
    if not by_chat:
        log(f"no links found (chat={chat_filter or 'ALL'})")
        return 0

    state = {} if args.refetch else load_state()
    done = state.setdefault("fetched", {})  # url -> {title, summary, category, status, fetched_at}

    total_new = 0
    for chat, links in by_chat.items():
        enriched: list[dict] = []
        new_here = 0
        for link in links:
            url = link["url"]
            link["category"] = category_for(link["domain"])
            if not domain_allowed(link["domain"], whitelist):
                link.update({"title": None, "summary": None, "status": "skipped_domain"})
                enriched.append(link)
                continue
            cached = done.get(url)
            if cached and not args.refetch:
                link.update({k: cached.get(k) for k in ("title", "summary", "status")})
            else:
                if args.limit and new_here >= args.limit:
                    # leave unfetched links in catalog with nulls; pick up next run
                    link.update({"title": None, "summary": None, "status": "pending"})
                    enriched.append(link)
                    continue
                res = fetch(url)
                link.update(res)
                done[url] = {"title": res["title"], "summary": res["summary"],
                             "category": link["category"], "status": res["status"],
                             "fetched_at": datetime.now(timezone.utc).isoformat()}
                new_here += 1
                total_new += 1
                log(f"[{chat}] {res['status']:14s} {url[:70]}")
                time.sleep(FETCH_DELAY)
            enriched.append(link)
            # 링크 노트 정본은 build_link_nodes.py가 이 카탈로그를 읽어 knowledge/links/nodes/로
            # URL당 1개(dedup)로 생성한다. 여기선 카탈로그(json)만 만든다.
            # (구: write_note로 knowledge/links/ 루트에 채팅방별 중복 md를 뿌렸으나 nodes/로 대체·제거)

        try:
            cat = write_catalog(chat, enriched)
            log(f"[{chat}] catalog -> {cat.name} ({len(enriched)} links, {new_here} new)")
        except Exception as e:  # 한 방의 카탈로그 실패가 전체 run을 죽이지 않게
            log(f"[{chat}] WARN catalog failed: {type(e).__name__}: {e}")
        save_state(state)  # 방마다 체크포인트 — 크래시/중단 시 fetch 진행 보존(끝에만 저장하던 버그)

    save_state(state)
    log(f"done: {total_new} new links fetched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
