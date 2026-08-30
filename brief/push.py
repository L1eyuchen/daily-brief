"""推送：飞书自定义机器人 webhook 适配器。

飞书限频：单租户单机器人 100 次/分钟、5 次/秒，请求体不超过 20KB。
每天只推一到两条，远在限制之内。
"""

from __future__ import annotations

import json
import logging

import requests

log = logging.getLogger("brief")

TIMEOUT = 20


def push_feishu(webhook: str, payload: dict, dry_run: bool = False) -> bool:
    """推送一条消息。dry_run 只打印不发送。成功返回 True。"""
    if dry_run or not webhook:
        log.info("[dry-run] 跳过实际推送，消息体如下：")
        log.info(json.dumps(payload, ensure_ascii=False, indent=2))
        return True

    try:
        resp = requests.post(webhook, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        log.error("推送请求失败：%s: %s", type(exc).__name__, exc)
        return False

    # 飞书两种返回格式都要兼容：{"code":0} 与 {"StatusCode":0}
    code = body.get("code", body.get("StatusCode"))
    if code == 0:
        log.info("推送成功")
        return True

    log.error("推送被拒绝：%s", json.dumps(body, ensure_ascii=False))
    return False


def _row_text(rows: list[list[dict]]) -> str:
    out = []
    for row in rows:
        out.append("".join(seg.get("text", "") for seg in row))
    return "\n".join(out)


def payload_size_ok(payload: dict) -> bool:
    """推送前自检：飞书要求请求体不超过 20KB。"""
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= 20 * 1024
