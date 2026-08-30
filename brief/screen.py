"""筛选：黑名单过滤 → 触发器打标 → 轨道判定 → 模型精筛（可关闭）。

设计要点：模型精筛是"增强"不是"必经"。API 挂掉、超预算或没配 Key 时，
自动退回纯关键词结果，简报照发——最贵的一环不能拖垮整体。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests

log = logging.getLogger("brief")

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
AI_TIMEOUT = 90

SYSTEM_PROMPT = """你是盖世小鸡（游戏手柄品牌）的内容运营助手。

判断一条资讯值不值得进简报，标准不是"这条新闻重不重要"，
而是"这条能不能变成小黑盒 / TapTap / 抖音 / B站 的一条具体内容"。

八条筛选触发器，命中任一条才可能入选：
1. 新游定档或发售——玩家会搜"XX 手柄能用吗""键位怎么设"，手柄类目 ROI 最高的长尾内容
2. 平台硬件变动——主机涨价、新主机、固件更新，直接影响兼容性与购买决策
3. 竞品新品或降价——竞品对位表是复用次数最高的一张表
4. 技术路线变化——TMR、霍尔、星闪、回报率标准一变，参数话术要跟着改
5. 社区高赞吐槽——真实痛点本身就是选题
6. 赛事与选手——格斗与 FPS 赛事节点是借势窗口
7. 供应链与政策——影响定价与备货，属于要转给内部的信息
8. 现象级可蹭热点——泛流量机会

给每一条打 0-3 分，不要做保留或丢弃的决定——取多少条由程序按分数排序后决定，
你只需要如实打分。

3 分（A 类）：大作的定档、发售、重制、移植、登陆新平台。
   玩家会立刻搜"XX 手柄能用吗""键位怎么设"，这是手柄类目 ROI 最高的内容。
   只要是知名 IP，哪怕是小作坊做的，也不算小众。

2 分（B 类）：赛车、格斗、魂类、动作类游戏的任何动态。
   这几类是手柄的强场景，天然对口，不必等它定档或发售。

1 分（C 类）：大 IP 的社区动态、版本更新、联动、厂商表态。
   能做话题内容，价值不如 A/B 直接，但值得让人看一眼。

0 分（丢弃）：
- 聚合类内容：周报、日报、播客、盘点、月度总结、编辑部闲聊、读者来信
- 真正的小众独立游戏。判断标准：说不出中文名，且没有知名发行商。
  反例（不算小众）：洛克人、恶魔城、The Crew、iRacing、女神异闻录、龙珠、
  我的英雄学院、真人快打等有中文认知度的 IP
- 纯评测、榜单、销量排行、投票
- 与游戏操作、硬件、平台生态无关的娱乐资讯（如配音演员个人动态）
- 同一事件的重复报道，只给信息更全的那条打分，其余给 0

三条硬规矩：
1. 判断必须基于摘要里真实出现的事实。摘要没写的东西不许推测、不许补充背景知识。
2. 落点要具体到"哪个平台的什么形式 + 大致内容方向"，不同条目之间不许套同一个模板。
3. 拿不准就给 1 分，不要给 0。漏掉一条真选题的代价，大于多看到一条待判断的。

只输出 JSON，不要任何解释文字。"""


def _build_user_prompt(
    items: list[dict], triggers: list[dict], min_items: int, max_items: int
) -> str:
    lines = ["今日候选条目：", ""]
    for i, item in enumerate(items):
        lines.append(f"[{i}] {item['title']}")
        if item.get("summary"):
            # 摘要太短模型认不出游戏类型（如分不清 The Crew 是赛车游戏），给足上下文
            lines.append(f"    摘要：{item['summary'][:200]}")
        lines.append(f"    来源：{item['source_name']}｜关键词命中：{', '.join(item.get('hits', [])) or '无'}")
        lines.append("")

    names = "\n".join(f"- {t['name']}：{t['why']}" for t in triggers)
    lines.append("触发器说明：")
    lines.append(names)
    lines.append("")
    lines.append("条目里的【关注名单】标记说明该条涉及重点 IP 或厂商。")
    lines.append("只要不是聚合类内容，带这个标记的都倾向保留——漏掉重点 IP 的代价更高。")
    lines.append("")
    lines.append(f"对上面每一条逐一打 0-3 分，一条都不能漏。程序会按分数取前 {max_items} 条。")
    lines.append("输出格式：")
    lines.append('{"results":[{"i":0,"score":3,')
    lines.append('"fact":"摘要中的关键事实，含具体数字或日期，25字内",')
    lines.append('"verdict":"这个事实对盖世小鸡意味着什么，25字内，不许复述标题",')
    lines.append('"slot":"平台+形式+内容方向","eta":"时效"}]}')
    lines.append("打 0 分的条目只写 {\"i\":n,\"score\":0}，不用给理由。")
    return "\n".join(lines)


class ScreenError(Exception):
    pass


def ai_judge(
    items: list[dict], triggers: list[dict], min_items: int, max_items: int
) -> dict[int, dict] | None:
    """调用 DeepSeek 精筛。失败一律返回 None 交给调用方降级，不抛异常。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        log.info("未配置 DEEPSEEK_API_KEY，跳过模型精筛，使用纯规则结果")
        return None

    user_prompt = _build_user_prompt(items, triggers, min_items, max_items)
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        resp = requests.post(DEEPSEEK_URL, json=payload, headers=headers, timeout=AI_TIMEOUT)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        log.warning("模型精筛失败，降级为纯规则：%s: %s", type(exc).__name__, exc)
        return None

    # 模型偶尔会给 JSON 包一层 markdown 代码块，先剥掉再解析
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    try:
        results = json.loads(cleaned).get("results", [])
    except json.JSONDecodeError as exc:
        log.warning("模型返回无法解析，降级为纯规则：%s", exc)
        return None

    result: dict[int, dict] = {}
    for row in results:
        try:
            idx = int(row["i"])
            score = int(row.get("score", 0))
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= idx < len(items)):
            continue
        # 打分制：0 分即丢弃，其余全交给调用方按分数排序截取
        if score <= 0:
            continue
        result[idx] = {
            "score": min(score, 3),
            "fact": str(row.get("fact", "")).strip(),
            "verdict": str(row.get("verdict", "")).strip(),
            "slot": str(row.get("slot", "")).strip(),
            "eta": str(row.get("eta", "")).strip(),
        }
    log.info("模型打分：%d / %d 条得分（程序按分数取前 %d）", len(result), len(items), max_items)
    return result


def apply_rules(items: list[dict], rules: dict) -> list[dict]:
    """黑名单过滤 + 触发器打标 + 轨道判定。"""
    blacklist = [w.lower() for w in rules.get("blacklist", [])]
    triggers = rules.get("triggers", [])
    controller_hints = [w.lower() for w in rules.get("controller_hints", [])]
    watchlist = rules.get("watchlist", [])

    kept: list[dict] = []
    blocked = 0
    for item in items:
        blob = f"{item['title']} {item.get('summary', '')}".lower()

        if any(word in blob for word in blacklist):
            blocked += 1
            continue

        hits = [t["id"] for t in triggers if any(k.lower() in blob for k in t["keywords"])]
        names = [t["name"] for t in triggers if t["id"] in hits]

        # 关注名单既兜底（捞回通用关键词漏掉的大 IP），也做标注。
        # 标注必须无条件打上：否则一条先命中触发器的大 IP 内容，模型看不到它是重点 IP，
        # 就容易因为摘要信息不足把它丢掉（The Crew、iRacing 都栽在这）。
        watched = [w for w in watchlist if w.lower() in blob]
        if watched:
            names.append("关注名单：" + "、".join(watched[:2]))

        if not hits and not watched:
            continue
        if watched and "watchlist" not in hits:
            hits.append("watchlist")

        is_controller = any(word in blob for word in controller_hints)
        item["hits"] = hits
        item["hit_names"] = names
        item["track"] = "controller" if is_controller else "game"
        kept.append(item)

    log.info("规则筛选：%d 条候选 -> %d 条命中（黑名单拦下 %d）", len(items), len(kept), blocked)
    return kept


def enrich_with_ai(items: list[dict], rules: dict, max_items: int) -> list[dict]:
    """给条目补上模型的判断、落点、时效。

    模型可用时以它的结论为准，被判 drop 的条目直接剔除——
    模型不可用时保留全部规则命中项，退回纯关键词模式。
    """
    delivery = rules.get("delivery", {})
    min_items = delivery.get("game_min_items", max_items)
    judged = ai_judge(items, rules.get("triggers", []), min_items, max_items)

    if judged is None:
        # 模型不可用：保留全部规则命中项，判断字段留空，退回纯关键词模式
        for item in items:
            item.update(fact="", verdict="", slot="", eta="", level="", judged_by="rule")
        items.sort(key=lambda x: (-len(x["hits"]), x["tier"]))
        return items

    # 模型可用：按"过滤前的索引"取回判断结果并即时写入条目，
    # 绝不能过滤后再用新索引去查——那会整组错位
    scored: list[dict] = []
    for idx, item in enumerate(items):
        pick = judged.get(idx)
        if not pick:
            continue
        item.update(
            score=pick["score"],
            # 2 分以上算确定，1 分标待判断，让人一眼分清
            level="high" if pick["score"] >= 2 else "maybe",
            fact=pick["fact"],
            verdict=pick["verdict"],
            slot=pick["slot"],
            eta=pick["eta"],
            judged_by="ai",
        )
        scored.append(item)

    if not scored:
        log.info("模型判定今日全部无落点，简报为空")
        return []

    scored.sort(key=lambda x: (-x["score"], -len(x["hits"]), x["tier"]))
    selected = scored[:max_items]
    high = sum(1 for x in selected if x["level"] == "high")
    log.info("选定 %d 条：确定 %d 条，待判断 %d 条", len(selected), high, len(selected) - high)
    if len(selected) < min_items:
        log.warning("今日仅 %d 条，低于目标下限 %d——可考虑放宽 rules.yaml", len(selected), min_items)
    return selected
