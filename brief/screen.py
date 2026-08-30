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

必须判为 drop 的情况：
- 小众独立游戏的发售、更新、配音、折扣——没有中文社区关注度，对手柄品牌零价值
- 聚合类内容：周报、播客、盘点、月度总结——信息密度高但无法直接变成一条内容
- 纯媒体评测、榜单、编辑推荐
- 与手柄、游戏操作、硬件、平台生态完全无关的娱乐资讯

三条硬规矩：
1. 判断必须基于摘要里真实出现的事实。摘要没写的东西不许推测、不许补充背景知识。
2. 摘要信息不足以支撑一个具体落点时，判 drop。宁缺毋滥，可以只留 2 条甚至 0 条。
3. 落点要具体到"哪个平台的什么形式 + 大致内容方向"，不同条目之间不许套同一个模板。
   如果两条的落点撞车，只保留价值更高的那条。

只输出 JSON，不要任何解释文字。"""


def _build_user_prompt(items: list[dict], triggers: list[dict]) -> str:
    lines = ["今日候选条目：", ""]
    for i, item in enumerate(items):
        lines.append(f"[{i}] {item['title']}")
        if item.get("summary"):
            lines.append(f"    摘要：{item['summary'][:120]}")
        lines.append(f"    来源：{item['source_name']}｜关键词命中：{', '.join(item.get('hits', [])) or '无'}")
        lines.append("")

    names = "\n".join(f"- {t['name']}：{t['why']}" for t in triggers)
    lines.append("触发器说明：")
    lines.append(names)
    lines.append("")
    lines.append("对上面每一条逐一判定，一条都不能漏。输出格式：")
    lines.append('{"results":[{"i":0,"keep":true,"fact":"摘要中的关键事实，含具体数字或日期，25字内",')
    lines.append('"verdict":"这个事实对盖世小鸡意味着什么，25字内，不许复述标题",')
    lines.append('"slot":"平台+形式+内容方向","eta":"时效"}]}')
    lines.append("判 drop 的条目只写 {\"i\":n,\"keep\":false}，不用给理由。")
    return "\n".join(lines)


class ScreenError(Exception):
    pass


def ai_judge(items: list[dict], triggers: list[dict], max_items: int) -> dict[int, dict] | None:
    """调用 DeepSeek 精筛。失败一律返回 None 交给调用方降级，不抛异常。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        log.info("未配置 DEEPSEEK_API_KEY，跳过模型精筛，使用纯规则结果")
        return None

    user_prompt = _build_user_prompt(items, triggers)
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 1500,
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
        if len(result) >= max_items:
            break
        try:
            idx = int(row["i"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= idx < len(items)) or not row.get("keep"):
            continue
        result[idx] = {
            "fact": str(row.get("fact", "")).strip(),
            "verdict": str(row.get("verdict", "")).strip(),
            "slot": str(row.get("slot", "")).strip(),
            "eta": str(row.get("eta", "")).strip(),
        }
    log.info("模型精筛：保留 %d / %d 条（上限 %d）", len(result), len(items), max_items)
    return result


def apply_rules(items: list[dict], rules: dict) -> list[dict]:
    """黑名单过滤 + 触发器打标 + 轨道判定。"""
    blacklist = [w.lower() for w in rules.get("blacklist", [])]
    triggers = rules.get("triggers", [])
    controller_hints = [w.lower() for w in rules.get("controller_hints", [])]

    kept: list[dict] = []
    blocked = 0
    for item in items:
        blob = f"{item['title']} {item.get('summary', '')}".lower()

        if any(word in blob for word in blacklist):
            blocked += 1
            continue

        hits = [t["id"] for t in triggers if any(k.lower() in blob for k in t["keywords"])]
        if not hits:
            continue

        is_controller = any(word in blob for word in controller_hints)
        item["hits"] = hits
        item["hit_names"] = [
            t["name"] for t in triggers if t["id"] in hits
        ]
        item["track"] = "controller" if is_controller else "game"
        kept.append(item)

    log.info("规则筛选：%d 条候选 -> %d 条命中（黑名单拦下 %d）", len(items), len(kept), blocked)
    return kept


def enrich_with_ai(items: list[dict], rules: dict, max_items: int) -> list[dict]:
    """给条目补上模型的判断、落点、时效。

    模型可用时以它的结论为准，被判 drop 的条目直接剔除——
    模型不可用时保留全部规则命中项，退回纯关键词模式。
    """
    judged = ai_judge(items, rules.get("triggers", []), max_items)

    if judged is None:
        # 模型不可用：保留全部规则命中项，判断字段留空，退回纯关键词模式
        for item in items:
            item.update(fact="", verdict="", slot="", eta="", judged_by="rule")
        items.sort(key=lambda x: (-len(x["hits"]), x["tier"]))
        return items

    # 模型可用：按"过滤前的索引"取回判断结果并即时写入条目，
    # 绝不能过滤后再用新索引去查——那会整组错位
    kept: list[dict] = []
    for idx, item in enumerate(items):
        pick = judged.get(idx)
        if not pick:
            continue
        item.update(
            fact=pick["fact"],
            verdict=pick["verdict"],
            slot=pick["slot"],
            eta=pick["eta"],
            judged_by="ai",
        )
        kept.append(item)

    if not kept:
        log.info("模型判定今日全部无落点，简报为空")
        return []

    kept.sort(key=lambda x: (-len(x["hits"]), x["tier"]))
    return kept
