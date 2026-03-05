from __future__ import annotations

"""Logging configuration for XiaoPaw.

- 控制台：人类可读格式
- 文件：JSON 行日志，写入 data/logs/xiaopaw.log（滚动）
"""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """简单的 JSON 日志 formatter."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # 附加常见上下文字段（如有）
        for key in ("routing_key", "session_id", "feishu_msg_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(log_dir: Path) -> None:
    """初始化文件日志（JSON 行格式）。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "xiaopaw.log"

    handler = RotatingFileHandler(
        log_path,
        maxBytes=50 * 1024 * 1024,  # 50MB
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.addHandler(handler)

