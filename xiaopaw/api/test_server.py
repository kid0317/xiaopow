"""TestAPI — 模拟飞书消息事件的 HTTP 测试接口

仅在 debug.enable_test_api: true 时由 main.py 启动。
"""

from __future__ import annotations

import asyncio
import time
import uuid

from aiohttp import web
from pydantic import ValidationError

from xiaopaw.api.capture_sender import CaptureSender
from xiaopaw.api.schemas import TestRequest, TestResponse
from xiaopaw.models import InboundMessage
from xiaopaw.session.manager import SessionManager


_DEFAULT_TIMEOUT = 300.0  # 等待 Bot 回复的默认超时（秒）

_runner_key = web.AppKey("runner", object)
_sender_key = web.AppKey("sender", CaptureSender)
_session_mgr_key = web.AppKey("session_mgr", object)


def create_test_app(
    runner: object,
    sender: CaptureSender,
    session_mgr: SessionManager | None = None,
) -> web.Application:
    """创建 aiohttp 应用，注入 Runner、CaptureSender、SessionManager。"""
    app = web.Application()
    app[_runner_key] = runner
    app[_sender_key] = sender
    if session_mgr is not None:
        app[_session_mgr_key] = session_mgr

    app.router.add_post("/api/test/message", _handle_message)
    app.router.add_delete("/api/test/sessions", _handle_delete_sessions)

    return app


async def _handle_message(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        req = TestRequest.model_validate(body)
    except ValidationError as e:
        return web.json_response(
            {"error": e.errors()}, status=422
        )

    msg_id = req.msg_id or f"test_{uuid.uuid4().hex[:12]}"
    ts_ms = int(time.time() * 1000)

    inbound = InboundMessage(
        routing_key=req.routing_key,
        content=req.content,
        msg_id=msg_id,
        root_id=msg_id,
        sender_id=req.sender_id,
        ts=ts_ms,
    )

    sender = request.app[_sender_key]
    runner = request.app[_runner_key]

    # 注册 Future，dispatch 完成后 send 会 resolve 它
    reply_fut = sender.register(msg_id)

    t_start = time.monotonic()
    await runner.dispatch(inbound)
    reply = await asyncio.wait_for(reply_fut, timeout=_DEFAULT_TIMEOUT)
    duration_ms = int((time.monotonic() - t_start) * 1000)

    # 从 SessionManager 获取当前 session_id
    session_id = ""
    if _session_mgr_key in request.app:
        session_mgr: SessionManager = request.app[_session_mgr_key]
        session = await session_mgr.get_or_create(req.routing_key)
        session_id = session.id

    resp = TestResponse(
        msg_id=msg_id,
        reply=reply,
        session_id=session_id,
        duration_ms=duration_ms,
        skills_called=[],  # TODO: 从 Trace 获取
    )
    return web.json_response(resp.model_dump())


async def _handle_delete_sessions(request: web.Request) -> web.Response:
    if _session_mgr_key in request.app:
        session_mgr: SessionManager = request.app[_session_mgr_key]
        await session_mgr.clear_all()
    return web.json_response({"status": "ok"})
