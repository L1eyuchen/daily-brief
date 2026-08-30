"""调试工具：打印进入模型精筛前的全部候选条目，用于判断阈值松紧。

调阈值时最容易犯的错，是只看最终留下几条就下结论——
留下 3 条，可能是模型太严，也可能候选里本来就只有 3 条值钱。
这个脚本把候选摊开，让你自己判断。

用法：
    .venv/Scripts/python.exe tools/inspect_candidates.py         # 只看规则层候选
    .venv/Scripts/python.exe tools/inspect_candidates.py --ai    # 附带模型逐条判定
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

from brief import load_env  # noqa: E402
from brief.fetch import fetch_all  # noqa: E402
from brief.screen import ai_judge, apply_rules  # noqa: E402
from brief.store import Store  # noqa: E402


def main() -> int:
    # 不加载 .env 的话模型精筛会被静默跳过，看上去像模型把全部条目判了 drop
    load_env(ROOT)
    sources_cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    rules = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
    store = Store(ROOT)

    items = fetch_all(sources_cfg, store)
    # 直接看规则层产出，跳过去重，便于观察全貌
    kept = apply_rules(items, rules)

    delivery = rules.get("delivery", {})
    max_items = delivery.get("game_max_items", 8)
    min_items = delivery.get("game_min_items", max_items)

    judged = None
    if "--ai" in sys.argv:
        judged = ai_judge(kept, rules.get("triggers", []), min_items, max_items)
        print(f"\n=== 模型打分：{len(judged) if judged else 0} / {len(kept)} 条有分 ===\n")

    print(f"\n=== 规则命中 {len(kept)} 条 ===\n")
    for i, item in enumerate(kept):
        if judged is not None:
            pick = judged.get(i)
            mark = f"[{pick['score']}分]" if pick else "[0分]"
        else:
            mark = ""
        print(f"[{i}] {mark} {item['title'][:62]}")
        print(f"    来源 {item['source_name']}｜命中 {'、'.join(item['hit_names'])}")
        if judged is not None and judged.get(i):
            pick = judged[i]
            print(f"    事实 {pick['fact']}")
            print(f"    落点 {pick['slot']}｜时效 {pick['eta']}")
        elif item.get("summary"):
            print(f"    摘要 {item['summary'][:110]}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
