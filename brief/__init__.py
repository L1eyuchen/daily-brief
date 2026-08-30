"""每日资讯简报。

环境变量加载放在包里而不是 main.py，是为了让 tools/ 下的调试脚本也能拿到配置——
否则脚本会静默跳过模型精筛，看上去像是模型把全部条目判了 drop。
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env(root: Path) -> None:
    """极简 .env 加载，避免为一个文件引入 python-dotenv 依赖。

    已存在的环境变量优先（GitHub Actions 用 Secrets 注入时不该被本地文件覆盖）。
    """
    path = Path(root) / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
