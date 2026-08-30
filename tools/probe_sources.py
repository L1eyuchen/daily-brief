"""资讯源体检工具：批量实测候选地址，输出可用性与新鲜度。

用法：
    .venv/Scripts/python.exe tools/probe_sources.py              # 只测配置里已启用的源
    .venv/Scripts/python.exe tools/probe_sources.py --candidates # 测下面 CANDIDATES 里的候选源

源清单会过期。README 里建议每季度重跑一次，别让它变成一份自己都不信的文档。
"""

from __future__ import annotations

import concurrent.futures as futures
import sys
from pathlib import Path

import feedparser
import requests

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (compatible; GameSirBrief/1.0)"
TIMEOUT = 25

# 候选源：加源前先在这里跑一遍，确认能拉到结构化内容再写进 sources.yaml
CANDIDATES = [
    # 赛事与电竞
    ("Dot Esports", "https://dotesports.com/feed"),
    ("Esports Insider", "https://esportsinsider.com/feed"),
    ("ESPN Esports", "https://www.espn.com/esports/rss"),
    ("HLTV", "https://www.hltv.org/rss/news"),
    ("EventHubs 格斗", "https://www.eventhubs.com/news/rss/"),
    ("Esports Charts", "https://escharts.com/news/rss"),
    # 综合补量
    ("GameSpot", "https://www.gamespot.com/feeds/news/"),
    ("Polygon", "https://www.polygon.com/rss/index.xml"),
    ("VG247", "https://www.vg247.com/feed"),
    ("Dexerto", "https://www.dexerto.com/feed/"),
    ("Game Rant", "https://gamerant.com/feed/"),
    ("游民星空", "https://www.gamersky.com/rss/news.xml"),
    ("3DMGAME", "https://www.3dmgame.com/rss/news.xml"),
    ("A9VG", "https://www.a9vg.com/rss"),
    ("篝火营地", "https://gouhuo.qq.com/rss"),
]


def probe(name: str, url: str) -> tuple[str, str, str, int, str, str]:
    """返回 (名称, 结果, 状态码/异常, 条目数, 最新标题, 最新时间)"""
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        if resp.status_code != 200:
            return name, "HTTP", str(resp.status_code), 0, "", ""
        parsed = feedparser.parse(resp.content)
        entries = parsed.entries
        if not entries:
            return name, "空", "0 条", 0, "", ""
        top = entries[0]
        return (
            name,
            "可用",
            "200",
            len(entries),
            (top.get("title") or "")[:44],
            top.get("published") or top.get("updated") or "无时间",
        )
    except Exception as exc:
        return name, "失败", f"{type(exc).__name__}", 0, str(exc)[:50], ""


def load_enabled() -> list[tuple[str, str]]:
    import yaml

    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    return [(s["name"], s["url"]) for s in cfg["sources"] if s.get("enabled", True)]


def main() -> int:
    use_candidates = "--candidates" in sys.argv
    targets = CANDIDATES if use_candidates else load_enabled()
    label = "候选源" if use_candidates else "已启用源"

    print(f"=== 体检 {label}：{len(targets)} 个 ===")
    rows = []
    with futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(probe, n, u) for n, u in targets]
        for job in futures.as_completed(jobs):
            rows.append(job.result())

    rows.sort(key=lambda r: (r[1] != "可用", r[0]))
    for name, status, code, count, title, when in rows:
        if status == "可用":
            print(f"[{status}] {name:<18} {code}  {count:>3} 条  最新 {when}")
            print(f"          └ {title}")
        else:
            print(f"[{status}] {name:<18} {code}")

    ok = sum(1 for r in rows if r[1] == "可用")
    print(f"\n可用 {ok} / {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
