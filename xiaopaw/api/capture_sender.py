"""CaptureSender — 测试模式下替换 FeishuSender，捕获回复到 Future"""

from __future__ import annotations

import asyncio


class CaptureSender:
    """通过 asyncio.Future 捕获 Bot 回复，供 TestAPI 同步返回。

    用法:
        sender = CaptureSender()
        fut = sender.register("msg_001")
        # ... Runner 处理消息 ...
        # Runner 内部调用 sender.send(..., root_id="msg_001")
        reply = await sender.wait_for_reply("msg_001", timeout=30)
    """

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[str]] = {}

    def register(self, msg_id: str) -> asyncio.Future[str]:
        """注册一个 msg_id，返回对应的 Future。重复注册会覆盖旧 Future。"""
        fut = asyncio.get_running_loop().create_future()
        self._futures[msg_id] = fut
        return fut

    async def send(
        self, routing_key: str, content: str, root_id: str
    ) -> None:
        """实现 SenderProtocol。将 content resolve 到对应 Future。"""
        fut = self._futures.pop(root_id, None)
        if fut is not None and not fut.done():
            fut.set_result(content)

    async def wait_for_reply(self, msg_id: str, timeout: float) -> str:
        """等待 msg_id 对应的回复，超时抛出 asyncio.TimeoutError。"""
        fut = self._futures.get(msg_id)
        if fut is None:
            raise KeyError(f"msg_id {msg_id!r} 未注册")
        return await asyncio.wait_for(fut, timeout=timeout)
