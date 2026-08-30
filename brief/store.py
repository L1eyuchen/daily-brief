"""状态存储：去重记录、轨迹待发池、源健康分、每日归档。

关键前提：GitHub Actions 的容器每次运行都是干净的文件系统，
跨天状态必须落盘并随仓库提交，否则去重失效、重复推送。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SEEN_KEEP_DAYS = 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def url_key(url: str) -> str:
    """归一化 URL 后再取哈希。

    去掉 utm_* 等追踪参数和锚点，否则同一条新闻换个参数就会被视为新条目重复推送。
    """
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    clean = parts._replace(query=urlencode(kept), fragment="")
    return hashlib.sha1(urlunsplit(clean).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.state_dir = root / "state"
        self.archive_dir = root / "archive"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    # ---- 底层读写 ----

    def _read(self, name: str, default):
        path = self.state_dir / name
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # 状态文件损坏时不能让整条流水线停摆，退回默认值即可
            return default

    def _write(self, name: str, data) -> None:
        path = self.state_dir / name
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    # ---- 去重记录 ----

    def load_seen(self) -> dict[str, str]:
        return self._read("seen.json", {})

    def mark_seen(self, keys: list[str]) -> None:
        seen = self.load_seen()
        stamp = utcnow().isoformat()
        for k in keys:
            seen[k] = stamp
        self._prune(seen)
        self._write("seen.json", seen)

    def _prune(self, seen: dict[str, str]) -> None:
        """丢弃过期记录，防止 seen.json 无限增长。"""
        cutoff = utcnow() - timedelta(days=SEEN_KEEP_DAYS)
        for k, v in list(seen.items()):
            try:
                if datetime.fromisoformat(v) < cutoff:
                    del seen[k]
            except ValueError:
                del seen[k]

    # ---- 待发池：controller 轨道攒够再发 ----

    def load_pending(self, track: str) -> list[dict]:
        return self._read(f"pending_{track}.json", [])

    def add_pending(self, track: str, items: list[dict]) -> None:
        pool = self.load_pending(track)
        pool.extend(items)
        self._write(f"pending_{track}.json", pool)

    def clear_pending(self, track: str) -> None:
        self._write(f"pending_{track}.json", [])

    # ---- 源健康分 ----

    def load_health(self) -> dict[str, int]:
        return self._read("health.json", {})

    def bump_fail(self, source_id: str) -> None:
        health = self.load_health()
        health[source_id] = health.get(source_id, 0) + 1
        self._write("health.json", health)

    def reset_fail(self, source_id: str) -> None:
        health = self.load_health()
        if health.pop(source_id, None) is not None:
            self._write("health.json", health)

    def unhealthy(self, threshold: int) -> list[str]:
        return [sid for sid, n in self.load_health().items() if n >= threshold]

    # ---- 计数器：用于手柄情报的期号 ----

    def bump_counter(self, name: str) -> int:
        data = self._read(f"counter_{name}.json", {"n": 0})
        data["n"] = int(data.get("n", 0)) + 1
        self._write(f"counter_{name}.json", data)
        return data["n"]

    # ---- 归档 ----

    def archive(self, day: str, filename: str, content: str) -> Path:
        folder = self.archive_dir / day[:4]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / filename
        path.write_text(content, encoding="utf-8")
        return path
