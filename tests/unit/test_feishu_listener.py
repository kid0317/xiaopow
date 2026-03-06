"""FeishuListener 单元测试"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xiaopaw.feishu.listener import FeishuListener, _XiaoPawEventHandler, run_forever
from xiaopaw.models import Attachment, InboundMessage


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


# ── _extract_attachment ────────────────────────────────────────────────────────


class TestFeishuListenerExtractAttachment:
    """_extract_attachment 静态方法测试（覆盖 lines 147-169）"""

    def test_text_type_returns_none(self):
        assert FeishuListener._extract_attachment("text", '{"text": "hello"}') is None

    def test_unknown_type_returns_none(self):
        assert FeishuListener._extract_attachment("post", "{}") is None

    def test_empty_content_returns_none_for_image(self):
        assert FeishuListener._extract_attachment("image", "") is None

    def test_empty_content_returns_none_for_file(self):
        assert FeishuListener._extract_attachment("file", "") is None

    def test_invalid_json_returns_none(self):
        assert FeishuListener._extract_attachment("image", "not-json{") is None

    def test_image_with_key_returns_attachment(self):
        content = json.dumps({"image_key": "img_key_abc"})
        result = FeishuListener._extract_attachment("image", content)
        assert result is not None
        assert result.msg_type == "image"
        assert result.file_key == "img_key_abc"
        assert result.file_name == "img_key_abc.jpg"

    def test_image_without_key_returns_none(self):
        content = json.dumps({"other": "data"})
        assert FeishuListener._extract_attachment("image", content) is None

    def test_image_with_empty_key_returns_none(self):
        content = json.dumps({"image_key": ""})
        assert FeishuListener._extract_attachment("image", content) is None

    def test_file_with_key_returns_attachment(self):
        content = json.dumps({"file_key": "file_abc", "file_name": "report.pdf"})
        result = FeishuListener._extract_attachment("file", content)
        assert result is not None
        assert result.msg_type == "file"
        assert result.file_key == "file_abc"
        assert result.file_name == "report.pdf"

    def test_file_without_name_uses_key(self):
        content = json.dumps({"file_key": "file_xyz"})
        result = FeishuListener._extract_attachment("file", content)
        assert result is not None
        assert result.file_name == "file_xyz"

    def test_file_without_key_returns_none(self):
        content = json.dumps({"file_name": "report.pdf"})
        assert FeishuListener._extract_attachment("file", content) is None

    def test_file_with_empty_key_returns_none(self):
        content = json.dumps({"file_key": "", "file_name": "report.pdf"})
        assert FeishuListener._extract_attachment("file", content) is None


# ── FeishuListener init & start ────────────────────────────────────────────────


class TestFeishuListenerInit:
    """FeishuListener.__init__ 测试（覆盖 lines 126-127）"""

    def test_init_creates_ws_client(self):
        with patch("xiaopaw.feishu.listener.WSClient") as mock_ws_cls, \
             patch("xiaopaw.feishu.listener._XiaoPawEventHandler") as mock_handler_cls:
            loop = MagicMock()
            on_msg = AsyncMock()
            listener = FeishuListener("app_id_test", "app_secret_test", on_msg, loop)

            # _XiaoPawEventHandler 应以 loop 和 on_message 构建
            mock_handler_cls.assert_called_once_with(loop=loop, on_message=on_msg)
            # WSClient 应以飞书凭证和 handler 构建
            mock_ws_cls.assert_called_once()
            ws_kwargs = mock_ws_cls.call_args.kwargs
            assert ws_kwargs["app_id"] == "app_id_test"
            assert ws_kwargs["app_secret"] == "app_secret_test"


class TestFeishuListenerStart:
    """FeishuListener.start() 测试（覆盖 lines 136-140）"""

    async def test_start_runs_ws_client_in_executor(self):
        with patch("xiaopaw.feishu.listener.WSClient") as mock_ws_cls, \
             patch("xiaopaw.feishu.listener._XiaoPawEventHandler"):
            loop = asyncio.get_running_loop()
            mock_ws_instance = MagicMock()
            mock_ws_cls.return_value = mock_ws_instance

            listener = FeishuListener("app_id", "app_secret", AsyncMock(), loop)

            with patch.object(loop, "run_in_executor", new=AsyncMock()) as mock_exec:
                await listener.start()
                # start() 应调用 run_in_executor 并传入 ws_client.start
                mock_exec.assert_awaited_once()
                _, fn = mock_exec.call_args.args
                assert fn == mock_ws_instance.start


# ── run_forever ─────────────────────────────────────────────────────────────


class TestRunForever:
    """run_forever 包装函数测试（覆盖 lines 197-199）"""

    async def test_run_forever_calls_start_once(self):
        """run_forever 调用 listener.start()，成功后循环；CancelledError 退出"""
        listener = MagicMock()
        # 第一次成功，第二次抛 CancelledError 退出 while True
        listener.start = AsyncMock(side_effect=[None, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await run_forever(listener)

        assert listener.start.call_count >= 1


# ── _XiaoPawEventHandler exception branch ─────────────────────────────────────


class TestHandlerExceptionInBody:
    """do_without_validation 内部异常不向上传播（覆盖 lines 105-106）"""

    async def test_exception_in_handler_body_does_not_raise(self):
        loop = asyncio.get_event_loop()
        handler = _XiaoPawEventHandler(loop=loop, on_message=AsyncMock())

        # 让 resolve_routing_key 抛异常，触发 except 分支
        with patch(
            "xiaopaw.feishu.listener.resolve_routing_key",
            side_effect=RuntimeError("unexpected error"),
        ):
            # should NOT raise — exception is caught and logged
            handler.do_without_validation(_make_im_receive_payload())
