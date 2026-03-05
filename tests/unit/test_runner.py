"""Runner 单元测试"""

from __future__ import annotations

import asyncio

import pytest

from xiaopaw.models import InboundMessage
from xiaopaw.runner import Runner
from xiaopaw.session.manager import SessionManager
from xiaopaw.session.models import MessageEntry


# ── Test Helpers ──────────────────────────────────────────────


class MockSender:
    """可观测的 Sender，记录所有发送并通过 Queue 通知等待方"""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []
        self._queue: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()

    async def send(self, routing_key: str, content: str, root_id: str) -> None:
        msg = (routing_key, content, root_id)
        self.messages.append(msg)
        await self._queue.put(msg)

    async def wait_for_message(self, timeout: float = 2.0) -> tuple[str, str, str]:
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)


def make_inbound(
    routing_key: str = "p2p:ou_test",
    content: str = "hello",
    msg_id: str = "om_001",
) -> InboundMessage:
    return InboundMessage(
        routing_key=routing_key,
        content=content,
        msg_id=msg_id,
        root_id=msg_id,
        sender_id="ou_test",
        ts=1000000,
    )


async def echo_agent(
    user_message: str, history: list[MessageEntry], session_id: str
) -> str:
    return f"echo: {user_message}"


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def mock_sender():
    return MockSender()


@pytest.fixture
def session_mgr(tmp_path):
    return SessionManager(data_dir=tmp_path)


@pytest.fixture
async def runner(session_mgr, mock_sender):
    r = Runner(
        session_mgr=session_mgr,
        sender=mock_sender,
        agent_fn=echo_agent,
        idle_timeout=2.0,
    )
    yield r
    await r.shutdown()


# ── Slash Commands ────────────────────────────────────────────


class TestSlashNew:
    async def test_creates_new_session(self, runner, mock_sender, session_mgr):
        """发送普通消息后 /new，session 应切换"""
        await runner.dispatch(make_inbound(content="hi"))
        await mock_sender.wait_for_message()

        s1 = await session_mgr.get_or_create("p2p:ou_test")

        await runner.dispatch(make_inbound(content="/new", msg_id="om_002"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "新对话" in reply or "已创建" in reply

        s2 = await session_mgr.get_or_create("p2p:ou_test")
        assert s2.id != s1.id

    async def test_new_on_fresh_routing_key(self, runner, mock_sender, session_mgr):
        """/new 即使是全新 routing_key 也应成功"""
        await runner.dispatch(make_inbound(content="/new"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "新对话" in reply or "已创建" in reply

        session = await session_mgr.get_or_create("p2p:ou_test")
        assert session.id.startswith("s-")


class TestSlashVerbose:
    async def test_verbose_on(self, runner, mock_sender, session_mgr):
        """/verbose on 开启详细模式"""
        await runner.dispatch(make_inbound(content="/verbose on"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "开启" in reply

        session = await session_mgr.get_or_create("p2p:ou_test")
        assert session.verbose is True

    async def test_verbose_off(self, runner, mock_sender, session_mgr):
        """/verbose off 关闭详细模式"""
        # 先开启
        await runner.dispatch(make_inbound(content="/verbose on"))
        await mock_sender.wait_for_message()

        # 再关闭
        await runner.dispatch(make_inbound(content="/verbose off", msg_id="om_002"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "关闭" in reply

        session = await session_mgr.get_or_create("p2p:ou_test")
        assert session.verbose is False

    async def test_verbose_query_default(self, runner, mock_sender):
        """/verbose 查询默认状态（关闭）"""
        await runner.dispatch(make_inbound(content="/verbose"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "关闭" in reply

    async def test_verbose_query_after_on(self, runner, mock_sender):
        """/verbose 查询开启后的状态"""
        await runner.dispatch(make_inbound(content="/verbose on"))
        await mock_sender.wait_for_message()

        await runner.dispatch(make_inbound(content="/verbose", msg_id="om_002"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "开启" in reply


class TestSlashHelp:
    async def test_returns_command_list(self, runner, mock_sender):
        """/help 返回包含所有命令的说明"""
        await runner.dispatch(make_inbound(content="/help"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "/new" in reply
        assert "/verbose" in reply
        assert "/help" in reply
        assert "/status" in reply


class TestSlashStatus:
    async def test_returns_session_info(self, runner, mock_sender):
        """/status 返回当前 session 信息"""
        # 先发一条消息，产生 session
        await runner.dispatch(make_inbound(content="hi"))
        await mock_sender.wait_for_message()

        await runner.dispatch(make_inbound(content="/status", msg_id="om_002"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "s-" in reply  # session id


class TestSlashNotCommand:
    async def test_non_slash_goes_to_agent(self, runner, mock_sender):
        """非 slash command 的消息正常进入 agent"""
        await runner.dispatch(make_inbound(content="普通消息"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert reply == "echo: 普通消息"

    async def test_unknown_slash_goes_to_agent(self, runner, mock_sender):
        """未知 slash command 进入 agent 而非报错"""
        await runner.dispatch(make_inbound(content="/unknown"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert reply == "echo: /unknown"


# ── Dispatch + Queue ──────────────────────────────────────────


class TestDispatch:
    async def test_creates_queue_and_processes(self, runner, mock_sender):
        """dispatch 后消息应被处理"""
        await runner.dispatch(make_inbound())
        _, reply, _ = await mock_sender.wait_for_message()

        assert reply == "echo: hello"

    async def test_serial_within_routing_key(self, runner, mock_sender):
        """同一 routing_key 的消息串行处理，按顺序回复"""
        for i in range(3):
            await runner.dispatch(
                make_inbound(content=f"msg{i}", msg_id=f"om_{i}")
            )

        replies = []
        for _ in range(3):
            _, reply, _ = await mock_sender.wait_for_message()
            replies.append(reply)

        assert replies == ["echo: msg0", "echo: msg1", "echo: msg2"]

    async def test_parallel_across_routing_keys(self, runner, mock_sender):
        """不同 routing_key 的消息并行处理"""
        await runner.dispatch(
            make_inbound(routing_key="p2p:ou_a", content="a", msg_id="om_a")
        )
        await runner.dispatch(
            make_inbound(routing_key="p2p:ou_b", content="b", msg_id="om_b")
        )

        replies = set()
        for _ in range(2):
            _, reply, _ = await mock_sender.wait_for_message()
            replies.add(reply)

        assert replies == {"echo: a", "echo: b"}


# ── Handle Flow ───────────────────────────────────────────────


class TestHandle:
    async def test_sends_reply_with_correct_routing(self, runner, mock_sender):
        """回复发送到正确的 routing_key 和 root_id"""
        await runner.dispatch(make_inbound(content="world"))
        rk, reply, root_id = await mock_sender.wait_for_message()

        assert rk == "p2p:ou_test"
        assert reply == "echo: world"
        assert root_id == "om_001"

    async def test_appends_to_session_history(self, runner, mock_sender, session_mgr):
        """处理后 user + assistant 消息应写入 session 历史"""
        await runner.dispatch(make_inbound())
        await mock_sender.wait_for_message()

        session = await session_mgr.get_or_create("p2p:ou_test")
        history = await session_mgr.load_history(session.id)

        assert len(history) == 2
        assert history[0].role == "user"
        assert history[0].content == "hello"
        assert history[1].role == "assistant"
        assert history[1].content == "echo: hello"

    async def test_passes_history_to_agent(self, session_mgr, mock_sender):
        """第二条消息时 agent 应收到之前的历史"""

        async def history_agent(
            user_msg: str, history: list[MessageEntry], sid: str
        ) -> str:
            return f"history_len={len(history)}"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=history_agent,
            idle_timeout=2.0,
        )

        try:
            await runner.dispatch(make_inbound(content="first"))
            await mock_sender.wait_for_message()

            await runner.dispatch(
                make_inbound(content="second", msg_id="om_002")
            )
            _, reply, _ = await mock_sender.wait_for_message()

            assert reply == "history_len=2"
        finally:
            await runner.shutdown()

    async def test_error_in_agent_sends_error_message(
        self, session_mgr, mock_sender
    ):
        """agent 抛异常时应发送错误提示"""

        async def failing_agent(
            user_msg: str, history: list[MessageEntry], sid: str
        ) -> str:
            raise RuntimeError("Agent crashed")

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=failing_agent,
            idle_timeout=2.0,
        )

        try:
            await runner.dispatch(make_inbound())
            _, reply, _ = await mock_sender.wait_for_message()

            assert "出错" in reply or "重试" in reply
        finally:
            await runner.shutdown()

    async def test_slash_command_not_saved_to_history(
        self, runner, mock_sender, session_mgr
    ):
        """slash command 不应写入 session 历史"""
        await runner.dispatch(make_inbound(content="/help"))
        await mock_sender.wait_for_message()

        session = await session_mgr.get_or_create("p2p:ou_test")
        history = await session_mgr.load_history(session.id)

        assert len(history) == 0


# ── Worker Lifecycle ──────────────────────────────────────────


class TestWorkerLifecycle:
    async def test_idle_timeout_cleans_up(self, session_mgr, mock_sender):
        """worker 空闲超时后应自动清理"""
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=0.1,
        )

        try:
            await runner.dispatch(make_inbound())
            await mock_sender.wait_for_message()

            assert "p2p:ou_test" in runner._queues

            # 等待 idle timeout
            await asyncio.sleep(0.3)

            assert "p2p:ou_test" not in runner._queues
            assert "p2p:ou_test" not in runner._workers
        finally:
            await runner.shutdown()

    async def test_shutdown_cancels_workers(self, session_mgr, mock_sender):
        """shutdown 应取消所有 worker"""
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=10.0,
        )

        await runner.dispatch(make_inbound())
        await mock_sender.wait_for_message()

        assert len(runner._workers) == 1

        await runner.shutdown()

        assert len(runner._workers) == 0
        assert len(runner._queues) == 0

    async def test_worker_restarts_after_idle_timeout(
        self, session_mgr, mock_sender
    ):
        """worker 超时退出后，新消息应自动创建新 worker"""
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=0.1,
        )

        try:
            await runner.dispatch(make_inbound(content="first"))
            await mock_sender.wait_for_message()

            # 等待 worker 超时退出
            await asyncio.sleep(0.3)
            assert "p2p:ou_test" not in runner._workers

            # 再发消息，应自动创建新 worker
            await runner.dispatch(
                make_inbound(content="second", msg_id="om_002")
            )
            _, reply, _ = await mock_sender.wait_for_message()
            assert reply == "echo: second"
        finally:
            await runner.shutdown()

    async def test_concurrent_dispatch_same_key(self, runner, mock_sender):
        """并发 dispatch 到同一 routing_key 不应创建重复 worker"""
        await asyncio.gather(
            runner.dispatch(make_inbound(content="c0", msg_id="om_0")),
            runner.dispatch(make_inbound(content="c1", msg_id="om_1")),
            runner.dispatch(make_inbound(content="c2", msg_id="om_2")),
        )

        replies = []
        for _ in range(3):
            _, reply, _ = await mock_sender.wait_for_message()
            replies.append(reply)

        # 只有一个 worker
        assert len(runner._workers) == 1
        assert set(replies) == {"echo: c0", "echo: c1", "echo: c2"}
