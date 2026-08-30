"""去重：跨天去重（比对待发记录）与批内去重（同一条新闻常被多个源转载）。

两个维度都要做：
  1. URL 归一化哈希——挡住完全相同的链接；
  2. 标题相似度——挡住改了链接参数或换源转载的同一条新闻。
"""

from __future__ import annotations

import logging
from rapidfuzz import fuzz

log = logging.getLogger("brief")

TITLE_SIMILARITY = 90


def dedupe(items: list[dict], seen_keys: set[str]) -> list[dict]:
    fresh: list[dict] = []
    seen_titles: list[str] = []
    dropped_seen = 0
    dropped_dup = 0

    for item in items:
        if item["key"] in seen_keys:
            dropped_seen += 1
            continue

        title = item["title"]
        if any(fuzz.ratio(title, t) >= TITLE_SIMILARITY for t in seen_titles):
            dropped_dup += 1
            continue

        seen_titles.append(title)
        fresh.append(item)

    log.info(
        "去重：%d 条输入 -> %d 条候选（历史重复 %d，批内重复 %d）",
        len(items), len(fresh), dropped_seen, dropped_dup,
    )
    return fresh
