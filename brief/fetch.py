"""抓取与归一化：并发拉取 RSS，输出统一结构的条目。

RSS 里能拿到什么、拿不到什么（2026-08-30 实测结论）：
  - 图片：能拿到 URL，来源有 media:content、media:thumbnail、enclosure、
    或摘要 HTML 里的 img/srcset。但存在懒加载（data-src）、相对路径、
    防盗链、CDN 签名过期四个坑，所以这里只提取 URL 不下载。
  - 视频：基本拿不到文件。RSS 给的是 YouTube / B站 的页面链接，
    带真实 mp4 附件的站点极少。故视频一律以"链接"形式呈现。
"""

from __future__ import annotations

import concurrent.futures as futures
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import requests

from .store import Store, url_key

log = logging.getLogger("brief")

IMG_RE = re.compile(
    r"<img[^>]+?(?:src|data-src|data-original|data-lazy-src)=[\"']([^\"']+)[\"']", re.I
)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
SUMMARY_MAX = 260


def _strip_html(raw: str) -> str:
    text = WS_RE.sub(" ", TAG_RE.sub(" ", html.unescape(raw or "")))
    return text.strip()


def _extract_cover(entry: Any) -> str | None:
    """按可靠性顺序找封面图 URL。只取 URL，不下载。"""
    for media in entry.get("media_content") or []:
        url = media.get("url")
        if url and (media.get("medium") == "image" or "image" in (media.get("type") or "")):
            return url

    for thumb in entry.get("media_thumbnail") or []:
        if thumb.get("url"):
            return thumb["url"]

    for enc in entry.get("enclosures") or []:
        if "image" in (enc.get("type") or "") and enc.get("href"):
            return enc["href"]

    for key in ("summary", "description", "content"):
        raw = entry.get(key)
        if isinstance(raw, list) and raw:
            raw = raw[0].get("value")
        if isinstance(raw, str):
            m = IMG_RE.search(raw)
            if m and m.group(1).startswith(("http://", "https://")):
                return m.group(1)
    return None


def _entry_time(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _normalize(entry: Any, source: dict) -> dict | None:
    title = _strip_html(entry.get("title") or "")
    link = entry.get("link") or ""
    if not title or not link:
        return None

    published = _entry_time(entry)
    summary = _strip_html(entry.get("summary") or entry.get("description") or "")

    return {
        "key": url_key(link),
        "title": title,
        "link": link,
        # 无时间戳的条目按"新鲜"处理，宁可多进一条也不漏
        "published": (published or datetime.now(timezone.utc)).isoformat(),
        "summary": summary[:SUMMARY_MAX],
        "cover": _extract_cover(entry),
        "source": source["id"],
        "source_name": source["name"],
        "tier": source.get("tier", 2),
        # 轨道先取源配置（game / esports / controller），筛选阶段可能再改判
        "track": source.get("track", "game"),
    }


def fetch_source(source: dict, fetch_cfg: dict, window_hours: int) -> tuple[list[dict], str | None]:
    """抓取单个源。返回值里 error 为 None 表示成功。"""
    timeout = fetch_cfg.get("timeout_seconds", 15)
    headers = {"User-Agent": fetch_cfg.get("user_agent", "GameSirBrief/1.0")}
    try:
        resp = requests.get(source["url"], headers=headers, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:  # 单个源失败不能拖垮整批
        return [], f"{type(exc).__name__}: {exc}"

    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        return [], f"解析失败: {parsed.get('bozo_exception')}"

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    items = []
    for entry in parsed.entries:
        item = _normalize(entry, source)
        if not item:
            continue
        if datetime.fromisoformat(item["published"]) < cutoff:
            continue
        items.append(item)
    return items, None


def fetch_all(config: dict, store: Store) -> list[dict]:
    sources = [s for s in config["sources"] if s.get("enabled", True)]
    fetch_cfg = config.get("fetch", {})
    window = fetch_cfg.get("window_hours", 24)
    workers = fetch_cfg.get("max_workers", 8)

    all_items: list[dict] = []
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(fetch_source, s, fetch_cfg, window): s for s in sources}
        for future in futures.as_completed(jobs):
            source = jobs[future]
            items, error = future.result()
            if error:
                store.bump_fail(source["id"])
                log.warning("[抓取失败] %s -> %s", source["name"], error)
                continue
            store.reset_fail(source["id"])
            log.info("[抓取成功] %s -> %d 条", source["name"], len(items))
            all_items.extend(items)

    log.info("共抓到 %d 条（窗口 %d 小时）", len(all_items), window)
    return all_items
