"""FeishuListener 单元测试"""

from __future__ import annotations

import asyncio
import json

import pytest

from xiaopaw.feishu.listener import FeishuListener, _XiaoPawEventHandler
from xiaopaw.models import InboundMessage


def _make_im_receive_payload(
    msg_type: str = "text",
    content: str = '{"text": "hello"}',
    chat_type: str = "p2p",
    sender_open_id: str = "ou_abc",
    chat_id: str = "oc_001",
    thread_id: str | None = None,
    message_id: str = "om_001",
    create_time: str = "1000000",
) -> bytes:
    data = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": sender_open_id}},
            "message": {
                "chat_type": chat_type,
                "chat_id": chat_id,
                "thread_id": thread_id,
                "message_type": msg_type,
                "content": content,
                "message_id": message_id,
                "root_id": message_id,
                "create_time": create_time,
            },
        },
    }
    return json.dumps(data).encode()


class TestXiaoPawEventHandlerInvalidInput:
    async def test_invalid_json_payload_does_not_raise(self):
        loop = asyncio.get_event_loop()
        on_msg = asyncio.coroutine(lambda _: None) if False else AsyncMockFn()
        handler = _XiaoPawEventHandler(loop=loop, on_message=on_msg.call)
        handler.do_without_validation(b"not-valid-json")
        # no exception = pass

    async def test_non_message_event_is_ignored(self):
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(loop=loop, on_message=on_msg)
        payload = json.dumps({
            "header": {"event_type": "bot.events.v1"},
            "event": {},
        }).encode()
        handler.do_without_validation(payload)
        await asyncio.sleep(0.05)
        assert received == []

    async def test_text_message_dispatched(self):
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(loop=loop, on_message=on_msg)
        handler.do_without_validation(_make_im_receive_payload())
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].content == "hello"
        assert received[0].routing_key == "p2p:ou_abc"
        assert received[0].msg_id == "om_001"
        assert received[0].ts == 1000000

    async def test_group_message_routing_key(self):
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(loop=loop, on_message=on_msg)
        handler.do_without_validation(
            _make_im_receive_payload(chat_type="group", chat_id="oc_group_001")
        )
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].routing_key == "group:oc_group_001"

    async def test_invalid_create_time_defaults_to_zero(self):
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(loop=loop, on_message=on_msg)
        handler.do_without_validation(
            _make_im_receive_payload(create_time="not_a_number")
        )
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].ts == 0


class AsyncMockFn:
    """轻量 async callable mock（不依赖 unittest.mock）。"""

    def __init__(self) -> None:
        self.calls: list = []

    async def call(self, arg: object) -> None:
        self.calls.append(arg)


class TestFeishuListenerExtractContent:
    def test_text_message(self):
        result = FeishuListener._extract_content("text", '{"text": "hello world"}')
        assert result == "hello world"

    def test_empty_content_returns_empty(self):
        result = FeishuListener._extract_content("text", "")
        assert result == ""

    def test_invalid_json_returns_empty(self):
        result = FeishuListener._extract_content("text", "not json {")
        assert result == ""

    def test_other_message_type_returns_empty(self):
        result = FeishuListener._extract_content("image", '{"image_key": "abc"}')
        assert result == ""

    def test_text_with_missing_text_key(self):
        result = FeishuListener._extract_content("text", '{"other": "value"}')
        assert result == ""

    def test_at_mention_text(self):
        result = FeishuListener._extract_content("text", '{"text": "@bot hello"}')
        assert result == "@bot hello"
