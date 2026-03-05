"""FeishuSender — 根据 routing_key 发送文本消息到飞书."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from lark_oapi.client import Client
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from xiaopaw.models import SenderProtocol

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FeishuSender(SenderProtocol):
    """基于 lark-oapi 的飞书发送实现."""

    client: Client
    max_retries: int = 3
    retry_backoff: tuple[int, ...] = (1, 2, 4)

    async def send(self, routing_key: str, content: str, root_id: str) -> None:
        msg_type = "text"
        msg_content = json.dumps({"text": content}, ensure_ascii=False)

        for attempt in range(self.max_retries):
            try:
                if routing_key.startswith("p2p:"):
                    receive_id = routing_key.split(":", 1)[1]
                    await self._send_p2p(receive_id, msg_type, msg_content, root_id)
                elif routing_key.startswith("group:"):
                    chat_id = routing_key.split(":", 1)[1]
                    await self._send_group(chat_id, msg_type, msg_content, root_id)
                elif routing_key.startswith("thread:"):
                    # thread:{chat_id}:{thread_id} — 话题内回复
                    await self._send_thread(root_id, msg_type, msg_content, root_id)
                else:
                    logger.warning("Unknown routing_key: %s", routing_key)
                return
            except Exception as exc:  # pragma: no cover - 网络错误在集成环境验证
                logger.warning(
                    "Failed to send message to %s (attempt %d/%d): %s",
                    routing_key,
                    attempt + 1,
                    self.max_retries,
                    exc,
                )
                if attempt + 1 >= self.max_retries:
                    break
                delay = self.retry_backoff[min(
                    attempt, len(self.retry_backoff) - 1
                )]
                await asyncio.sleep(delay)

    async def _send_p2p(
        self, open_id: str, msg_type: str, content: str, uuid: str
    ) -> None:
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(open_id)
            .msg_type(msg_type)
            .content(content)
            .uuid(uuid)
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(body)
            .build()
        )
        resp = await self.client.im.v1.message.acreate(req)
        if not resp.success():
            raise RuntimeError(f"Feishu create message failed: {resp.code}, {resp.msg}")

    async def _send_group(
        self, chat_id: str, msg_type: str, content: str, uuid: str
    ) -> None:
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type(msg_type)
            .content(content)
            .uuid(uuid)
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        resp = await self.client.im.v1.message.acreate(req)
        if not resp.success():
            raise RuntimeError(f"Feishu create message failed: {resp.code}, {resp.msg}")

    async def _send_thread(
        self, root_id: str, msg_type: str, content: str, uuid: str
    ) -> None:
        body = (
            ReplyMessageRequestBody.builder()
            .msg_type(msg_type)
            .content(content)
            .reply_in_thread(True)
            .uuid(uuid)
            .build()
        )
        req = (
            ReplyMessageRequest.builder()
            .message_id(root_id)
            .request_body(body)
            .build()
        )
        resp = await self.client.im.v1.message.areply(req)
        if not resp.success():
            raise RuntimeError(f"Feishu reply message failed: {resp.code}, {resp.msg}")

