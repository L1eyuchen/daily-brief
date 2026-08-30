"""每日简报编排入口。

流程：抓取 → 去重 → 规则筛选 → 模型精筛（可降级）→ 双轨分流 → 渲染 → 推送 → 归档。

双轨分流是核心设计：
  game 轨道命中即推（每日）；controller 轨道进待发池，攒满才发、没料就空着。
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import timedelta, timezone
from pathlib import Path

import yaml

from brief.dedupe import dedupe
from brief.fetch import fetch_all
from brief.push import payload_size_ok, push_feishu
from brief.render import render_feishu, render_markdown
from brief.screen import apply_rules, enrich_with_ai
from brief.store import Store, utcnow

ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("brief")


def load_env() -> None:
    """极简 .env 加载，避免为一个文件引入 python-dotenv 依赖。"""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_config(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


def deliver(store, webhook, dry_run, day, track, items, title, archive_name) -> bool:
    """渲染 → 自检体积 → 推送 → 归档。推送失败也照样归档，不留无痕的失败。"""
    payload = render_feishu(day, items, title)
    if not payload_size_ok(payload):
        log.error("[%s] 消息体超过飞书 20KB 限制，已跳过推送", track)
        return False

    ok = push_feishu(webhook, payload, dry_run=dry_run)
    store.archive(day, archive_name, render_markdown(day, items, title))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="盖世小鸡每日资讯简报")
    parser.add_argument("--dry-run", action="store_true", help="只跑流程不实际推送")
    args = parser.parse_args()

    load_env()
    sources_cfg = load_config("sources.yaml")
    rules = load_config("rules.yaml")
    store = Store(ROOT)

    webhook = os.environ.get("FEISHU_WEBHOOK", "")
    dry_run = args.dry_run
    if not dry_run and not webhook:
        log.warning("未配置 FEISHU_WEBHOOK，自动降级为 dry-run")
        dry_run = True

    day = utcnow().astimezone(CST).strftime("%Y-%m-%d")
    delivery_cfg = rules.get("delivery", {})
    max_items = delivery_cfg.get("game_max_items", 8)
    min_items = delivery_cfg.get("controller_min_items", 3)

    # 1–3 抓取、去重、规则筛选
    raw_items = fetch_all(sources_cfg, store)
    candidates = dedupe(raw_items, set(store.load_seen()))
    kept = apply_rules(candidates, rules)

    if not kept:
        log.info("今日无命中条目，不推送。没料就空着，不为凑数掺水。")
        return 0

    # 4 模型精筛（不可用时自动降级为纯规则）
    kept = enrich_with_ai(kept, rules, max_items)

    # 5 双轨分流
    game_items = [x for x in kept if x["track"] == "game"][:max_items]
    controller_items = [x for x in kept if x["track"] == "controller"]

    pushed = False

    if game_items:
        title = f"【{day} 主机/PC 游戏速览】共 {len(game_items)} 条"
        pushed |= deliver(
            store, webhook, dry_run, day, "game", game_items, title, f"{day}-game.md"
        )
    else:
        log.info("游戏轨道今日无命中，跳过")

    if controller_items:
        store.add_pending("controller", controller_items)
    pool = store.load_pending("controller")
    if len(pool) >= min_items:
        n = store.bump_counter("controller")
        title = f"【手柄情报 #{n:02d}】{day}"
        if deliver(
            store, webhook, dry_run, day, "controller", pool, title, f"{day}-controller.md"
        ):
            store.clear_pending("controller")
            pushed = True
    else:
        log.info(
            "手柄待发池 %d / %d 条，继续攒（没料就空着）", len(pool), min_items
        )

    # 6 所有通过筛选的条目一律记为已处理，避免重复入池或重复推送
    store.mark_seen([x["key"] for x in kept])

    # 7 源健康告警：连续失败的源要让人知道，否则静默失效
    threshold = sources_cfg.get("health", {}).get("fail_threshold", 3)
    broken = store.unhealthy(threshold)
    if broken:
        log.warning("以下源连续失败 %d 次以上：%s", threshold, "、".join(broken))
        alert = {
            "msg_type": "text",
            "content": {
                "text": f"[源健康告警] 以下源连续失败超过 {threshold} 次，建议检查或更换：\n"
                + "\n".join(f"- {sid}" for sid in broken)
            },
        }
        push_feishu(webhook, alert, dry_run=dry_run)

    log.info("完成：命中 %d 条，已推送 %s", len(kept), "是" if pushed else "否")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
