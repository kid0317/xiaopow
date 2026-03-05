"""XiaoPaw 进程入口（最小可用版本）

当前版本仅完成:
- 加载 config.yaml（feishu 配置 + data_dir）
- 初始化 SessionManager、Runner
- 使用简单 agent_fn：无视内容，统一回复“收到”
- 启动 FeishuListener，监听所有 p2p/group/thread 消息
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import yaml
from lark_oapi.client import Client, LogLevel

from xiaopaw.observability.logging_config import setup_logging
from xiaopaw.observability.metrics_server import start_metrics_server
from xiaopaw.feishu.downloader import FeishuDownloader
from xiaopaw.feishu.listener import FeishuListener, run_forever
from xiaopaw.feishu.sender import FeishuSender
from xiaopaw.runner import Runner
from xiaopaw.session.manager import SessionManager
from xiaopaw.session.models import MessageEntry

logger = logging.getLogger(__name__)


async def _shoudao_agent(
    user_message: str, history: list[MessageEntry], session_id: str
) -> str:
    """最小占位 agent：无论输入什么，都回复“收到 + session_id”."""
    return f"收到，session={session_id}"


def _load_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {config_path}. 请先创建配置文件。"
        )
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return data


async def async_main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    data_dir = (repo_root / "data").resolve()

    # 控制台日志 + JSON 文件日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    setup_logging(data_dir / "logs")
    config_path = repo_root / "config.yaml"
    cfg = _load_config(config_path)

    feishu_cfg = cfg.get("feishu", {})
    data_dir = Path(cfg.get("data_dir", "./data")).resolve()

    app_id = feishu_cfg.get("app_id")
    app_secret = feishu_cfg.get("app_secret")
    if not app_id or not app_secret:
        raise RuntimeError("feishu.app_id / feishu.app_secret 不能为空")

    # 构建 lark-oapi HTTP Client
    client = (
        Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(LogLevel.INFO)
        .build()
    )

    session_mgr = SessionManager(data_dir=data_dir)
    sender = FeishuSender(client=client)
    downloader = FeishuDownloader(client=client, data_dir=data_dir)
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=_shoudao_agent,
        downloader=downloader,
    )

    # WebSocket Listener 使用独立线程内的 lark-oapi 循环，通过主事件循环调度 Runner
    loop = asyncio.get_running_loop()
    listener = FeishuListener(
        app_id=app_id,
        app_secret=app_secret,
        on_message=runner.dispatch,
        loop=loop,
    )

    logger.info("XiaoPaw starting. Data dir: %s", data_dir)
    # 并行启动 Feishu WebSocket 监听与 /metrics 端点
    await asyncio.gather(
        run_forever(listener),
        start_metrics_server(host="127.0.0.1", port=9100),
    )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()

