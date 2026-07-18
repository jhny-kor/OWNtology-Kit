#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitlib.config import vault_path as _vp
VAULT = _vp()
SAFARI_DIR = VAULT / "source" / "safari-tabs"
KAKAO_DIR = VAULT / "ontology"
OUT_DIR = VAULT / "knowledge" / "links" / "nodes"
GITHUB_STARS_REPOS = VAULT / "knowledge" / "github-stars" / "repos"

MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+(?:\)[^)\s]+)*[^)\s]*)\)")
BARE_URL_RE = re.compile(r"(?<!\()https?://[^\s<>\"]+")
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


@dataclass
class LinkSource:
    kind: str
    date: str = ""
    title: str = ""
    chat: str = ""
    author: str = ""
    source_file: str = ""
    catalog: str = ""


@dataclass
class LinkNode:
    url: str
    title: str = ""
    summary: str = ""
    sources: list[LinkSource] = field(default_factory=list)


def quote_yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def normalize_url(url: str) -> str:
    url = url.strip().rstrip(".,;")
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return url
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    rebuilt = f"{scheme}://{netloc}{path}"
    if parsed.params:
        rebuilt += f";{parsed.params}"
    if parsed.query:
        rebuilt += f"?{parsed.query}"
    if parsed.fragment:
        rebuilt += f"#{parsed.fragment}"
    return rebuilt


def github_star_urls() -> set[str]:
    urls: set[str] = set()
    if not GITHUB_STARS_REPOS.exists():
        return urls
    for path in GITHUB_STARS_REPOS.glob("*.md"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"^url:\s*[\"']?([^\"'\n]+)", text, re.MULTILINE)
        if match:
            urls.add(normalize_url(match.group(1).strip()))
    return urls


def domain_for(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def display_url_for(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not parsed.query and not parsed.fragment:
        return url
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path or ''}"
    if parsed.params:
        base += f";{parsed.params}"
    if parsed.query:
        base += "?[query-redacted]"
    if parsed.fragment:
        base += "#[fragment-redacted]"
    return base


def slugify(text: str, fallback: str = "link") -> str:
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"[^\w가-힣.-]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    return (text or fallback)[:80]


def filename_for(node: LinkNode) -> str:
    digest = hashlib.sha1(node.url.encode("utf-8")).hexdigest()[:10]
    try:
        parsed = urlparse(node.url)
        path_hint = parsed.path
    except ValueError:
        path_hint = ""
    base = slugify(f"{domain_for(node.url)}-{path_hint or node.title}", "link")
    return f"{base}-{digest}.md"


def date_from_path(path: Path) -> str:
    match = DATE_RE.search(path.name)
    return match.group(1) if match else ""


def add_node(nodes: dict[str, LinkNode], url: str, title: str, source: LinkSource, summary: str = "") -> None:
    key = normalize_url(url)
    if not key.startswith(("http://", "https://")):
        return
    node = nodes.setdefault(key, LinkNode(url=key))
    clean_title = " ".join((title or "").split())
    if clean_title and (not node.title or len(clean_title) > len(node.title)):
        node.title = clean_title
    if summary and not node.summary:
        node.summary = summary.strip()
    source.title = clean_title
    node.sources.append(source)


def load_safari(nodes: dict[str, LinkNode]) -> None:
    if not SAFARI_DIR.exists():
        return
    for path in sorted(SAFARI_DIR.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(VAULT).as_posix()
        default_date = date_from_path(path)
        seen_spans: set[tuple[int, int]] = set()
        for match in MARKDOWN_LINK_RE.finditer(text):
            seen_spans.add(match.span(2))
            add_node(
                nodes,
                match.group(2),
                match.group(1),
                LinkSource(kind="safari", date=default_date, source_file=rel),
            )
        for match in BARE_URL_RE.finditer(text):
            if any(start <= match.start() < end for start, end in seen_spans):
                continue
            url = match.group(0).rstrip(").,;")
            add_node(
                nodes,
                url,
                domain_for(url),
                LinkSource(kind="safari", date=default_date, source_file=rel),
            )


def iter_kakao_catalogs() -> list[Path]:
    patterns = ["kakao-links*.json", "kakao-*-links*.json"]
    paths: dict[Path, None] = {}
    for pattern in patterns:
        for path in KAKAO_DIR.glob(pattern):
            if path.name.endswith(".tmp.json"):
                continue
            paths[path] = None
    return sorted(paths)


def load_kakao(nodes: dict[str, LinkNode]) -> None:
    for path in iter_kakao_catalogs():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        links = data.get("links") if isinstance(data, dict) else None
        if not isinstance(links, list):
            continue
        chat = str(data.get("chat") or "")
        source_file = str(data.get("source_file") or "")
        rel_catalog = path.relative_to(VAULT).as_posix()
        for link in links:
            if not isinstance(link, dict) or not link.get("url"):
                continue
            title = link.get("title") or link.get("url") or ""
            add_node(
                nodes,
                str(link["url"]),
                str(title),
                LinkSource(
                    kind="kakao",
                    date=str(link.get("date") or ""),
                    chat=chat,
                    author=str(link.get("author") or ""),
                    source_file=source_file,
                    catalog=rel_catalog,
                ),
                summary=str(link.get("summary") or ""),
            )


def first_seen(node: LinkNode) -> str:
    dates = sorted({source.date for source in node.sources if source.date})
    return dates[0] if dates else datetime.now().strftime("%Y-%m-%d")


def source_names(node: LinkNode) -> list[str]:
    return sorted({source.kind for source in node.sources})


def render_node(node: LinkNode) -> str:
    raw_title = node.title or node.url
    title = display_url_for(node.url) if raw_title.startswith(("http://", "https://")) else raw_title
    display_url = display_url_for(node.url)
    domain = domain_for(node.url)
    sources = source_names(node)
    source_yaml = "[" + ", ".join(quote_yaml(source) for source in sources) + "]"
    lines = [
        "---",
        f"title: {quote_yaml(title)}",
        "type: link",
        f"date: {first_seen(node)}",
        f"url: {quote_yaml(node.url)}",
        f"display_url: {quote_yaml(display_url)}",
        f"domain: {quote_yaml(domain)}",
        f"sources: {source_yaml}",
        f"source_count: {len(node.sources)}",
        "sensitivity: private",
        "---",
        "",
        f"# {title}",
        "",
        f"- URL: {display_url}",
        f"- 도메인: {domain}",
        f"- 출처: {', '.join(sources)}",
        "- 관련: [[통합 링크 노드]]",
        "- 태그: " + " ".join(f"#{source.replace('-', '_')}_link" for source in sources),
    ]
    if node.summary:
        lines += ["", "## 요약", "", node.summary]
    lines += ["", "## 출처 기록", ""]
    for source in sorted(node.sources, key=lambda item: (item.kind, item.date, item.chat, item.catalog)):
        parts = [source.kind]
        if source.date:
            parts.append(source.date)
        if source.chat:
            parts.append(f"chat={source.chat}")
        if source.author:
            parts.append(f"author={source.author}")
        if source.source_file:
            parts.append(f"source={source.source_file}")
        if source.catalog:
            parts.append(f"catalog={source.catalog}")
        lines.append("- " + " | ".join(parts))
    lines += ["", "## 메모", ""]
    return "\n".join(lines)


def render_index(nodes: dict[str, LinkNode]) -> str:
    generated_at = datetime.now().replace(microsecond=0).isoformat()
    counter = Counter(source for node in nodes.values() for source in source_names(node))
    domains = Counter(domain_for(node.url) for node in nodes.values())
    lines = [
        "---",
        'title: "통합 링크 노드"',
        "type: index",
        f"date: {generated_at[:10]}",
        'source: "kakao+safari"',
        "sensitivity: private",
        "---",
        "",
        "# 통합 링크 노드",
        "",
        f"- 생성시각: {generated_at}",
        f"- 링크 노드: {len(nodes)}개",
    ]
    for source, count in sorted(counter.items()):
        lines.append(f"- {source}: {count}개")
    lines += [
        "",
        "## 사용",
        "",
        "- 개별 링크 노드는 이 폴더의 Markdown 파일 1개가 링크 1개를 나타낸다.",
        "- 원 URL은 frontmatter `url`에 보존하고, 본문 표시 URL은 query/fragment를 마스킹한다.",
        "- 전체 2만 개 링크 목록은 README에 쓰지 않는다. 파일명/도메인 검색으로 찾는다.",
        "",
        "## 상위 도메인",
        "",
    ]
    for domain, count in domains.most_common(100):
        lines.append(f"- {domain}: {count}개")
    return "\n".join(lines) + "\n"


def build(dry_run: bool) -> tuple[int, Counter[str]]:
    nodes: dict[str, LinkNode] = {}
    load_safari(nodes)
    load_kakao(nodes)
    for url in github_star_urls():
        nodes.pop(url, None)
    counter = Counter(source for node in nodes.values() for source in source_names(node))
    if dry_run:
        return len(nodes), counter
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in OUT_DIR.glob("*.md"):
        path.unlink()
    for node in nodes.values():
        (OUT_DIR / filename_for(node)).write_text(render_node(node) + "\n", encoding="utf-8")
    (OUT_DIR / "README.md").write_text(render_index(nodes), encoding="utf-8")
    return len(nodes), counter


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one Markdown node per saved Kakao/Safari link.")
    parser.add_argument("--dry-run", action="store_true", help="Count links without writing files.")
    args = parser.parse_args()
    total, counter = build(dry_run=args.dry_run)
    detail = ", ".join(f"{source}={count}" for source, count in sorted(counter.items()))
    mode = "dry-run" if args.dry_run else "written"
    print(f"[links/nodes] {mode}: {total} nodes ({detail})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
