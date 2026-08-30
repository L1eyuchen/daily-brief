"""渲染：把筛选后的条目变成飞书富文本消息与 Markdown 存档。

飞书自定义机器人（webhook）支持的标签只有 text / a / at / img，
其中 img 必须传 image_key，而 image_key 只有自建应用才能上传。
所以这里不内嵌图片，封面图 URL 只写进 Markdown 存档。
"""

from __future__ import annotations

INTERNAL_HITS = {"supply_chain", "hardware"}


def _is_internal(item: dict) -> bool:
    """命中供应链或硬件变动的条目，通常要转给产品 / 电商同事。"""
    return bool(set(item.get("hits", [])) & INTERNAL_HITS)


def _line(text: str) -> list[dict]:
    return [{"tag": "text", "text": text}]


def render_feishu(day: str, items: list[dict], title: str) -> dict:
    """组装飞书 post 消息体。"""
    rows: list[list[dict]] = []

    for i, item in enumerate(items, 1):
        rows.append([
            {"tag": "text", "text": f"{i}. "},
            {"tag": "a", "text": item["title"], "href": item["link"]},
            {"tag": "text", "text": f"（{item['source_name']}）"},
        ])
        if item.get("verdict"):
            # 宽松档标出来，让人一眼分清哪几条还需自己判断
            tag = "（待判断）" if item.get("level") == "maybe" else ""
            rows.append(_line(f"   → 事实：{item['fact']}"))
            rows.append(_line(f"   → 判断：{item['verdict']}"))
            rows.append(_line(f"   → 落点：{item['slot']}{tag}｜时效：{item['eta']}"))
        else:
            rows.append(_line(f"   → 命中：{'、'.join(item.get('hit_names', []))}"))

    internal = [str(i) for i, it in enumerate(items, 1) if _is_internal(it)]
    if internal:
        rows.append(_line(f"■ 值得转给同事：第 {'、'.join(internal)} 条"))

    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": rows,
                }
            }
        },
    }


def render_markdown(day: str, items: list[dict], title: str) -> str:
    """存档用的 Markdown，比推送内容多出封面图 URL 与命中标记。"""
    lines = [f"# {title}", ""]

    for i, item in enumerate(items, 1):
        lines.append(f"## {i}. {item['title']}")
        lines.append("")
        lines.append(f"- 来源：{item['source_name']}（`{item['source']}`）")
        lines.append(f"- 原文：{item['link']}")
        lines.append(f"- 轨道：{item.get('track', 'game')}｜命中：{'、'.join(item.get('hit_names', []))}")
        if item.get("verdict"):
            lines.append(f"- 关键事实：{item['fact']}")
            lines.append(f"- 判断：{item['verdict']}")
            lines.append(f"- 落点：{item['slot']}｜时效：{item['eta']}")
            lines.append(f"- 分档：{item.get('level', '-')}｜判定方式：{item.get('judged_by', 'rule')}")
        if item.get("cover"):
            lines.append(f"- 封面：{item['cover']}")
        if item.get("summary"):
            lines.append(f"- 摘要：{item['summary']}")
        lines.append("")

    internal = [f"{i}. {it['title']}" for i, it in enumerate(items, 1) if _is_internal(it)]
    lines.append("## 值得转给同事")
    lines.append("")
    lines.extend(f"- {t}" for t in internal) if internal else lines.append("- 无")
    lines.append("")
    return "\n".join(lines)
