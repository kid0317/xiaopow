"""Main Crew — XiaoPaw 主协调 Crew

工厂模式：build_agent_fn() 返回一个 agent_fn 闭包供 Runner 注入。
每次请求动态构建 Crew 实例（防止 CrewAI 内部状态污染）。

Verbose 模式：
    当 session.verbose=True 时，step_callback 将 AgentAction.thought
    通过 FeishuSender 推送到飞书，让用户看到 Agent 的推理过程。

历史注入：
    只将最近 max_history_turns 条消息格式化后注入 task.description。
    超出部分保留在 JSONL 文件，可通过 history_reader Skill 按页查询。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from crewai import Agent, Crew, Process, Task
from crewai.agents.parser import AgentAction, AgentFinish

from xiaopaw.agents.models import MainTaskOutput
from xiaopaw.llm.aliyun_llm import AliyunLLM
from xiaopaw.models import SenderProtocol
from xiaopaw.runner import AgentFn
from xiaopaw.session.models import MessageEntry
from xiaopaw.tools.intermediate_tool import IntermediateTool

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent / "config"
_DEFAULT_MAX_HISTORY_TURNS = 20


# ── History formatting ─────────────────────────────────────────


def _format_history(
    history: list[MessageEntry],
    max_turns: int = _DEFAULT_MAX_HISTORY_TURNS,
) -> str:
    """将对话历史格式化为 LLM 可读文本，超出部分附加 Skill 提示。

    Args:
        history: 全部历史消息（role="user"|"assistant"）
        max_turns: 最多保留的最近消息条数

    Returns:
        格式化后的字符串，超出时包含 history_reader Skill 提示
    """
    if not history:
        return "（无历史记录）"

    truncated = len(history) > max_turns
    recent = history[-max_turns:] if truncated else history

    role_map = {"user": "用户", "assistant": "助手"}
    lines = [
        f"{role_map.get(entry.role, entry.role)}: {entry.content}"
        for entry in recent
    ]

    result = "\n".join(lines)

    if truncated:
        omitted = len(history) - max_turns
        result = (
            f"（已省略更早的 {omitted} 条消息。如需查阅，"
            "可通过 history_reader Skill 按页读取完整历史。）\n"
        ) + result

    return result


# ── Verbose step_callback ─────────────────────────────────────


def _make_step_callback(
    sender: SenderProtocol,
    routing_key: str,
    root_id: str,
) -> Any:
    """构建 verbose step_callback，将 Agent 推理步骤推送到飞书。

    仅处理 AgentAction（推理中间步骤），不推送 AgentFinish（最终答案）。
    """

    async def callback(step: AgentAction | AgentFinish) -> None:
        if not isinstance(step, AgentAction):
            return

        thought = step.thought.strip()
        if not thought:
            return

        try:
            await sender.send(routing_key, f"💭 {thought}", root_id)
        except Exception:
            logger.warning(
                "verbose callback: failed to send thought to Feishu",
                exc_info=True,
            )

    return callback


# ── Crew builder ──────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _build_crew(
    session_id: str,
    history_all: list | None = None,
    step_callback: Any | None = None,
    extra_tools: list | None = None,
    sandbox_url: str = "",
) -> Crew:
    """构建主 Crew 实例（每次调用返回新实例，防止状态污染）。

    Args:
        session_id: 当前会话 ID（注入到 SkillLoaderTool，不传入 LLM 上下文）
        history_all: 完整历史消息列表（供 history_reader Skill 内联分页使用）
        step_callback: verbose 模式回调，None 表示关闭
        extra_tools: 额外注入的工具（测试用）
        sandbox_url: AIO-Sandbox MCP 端点 URL，空字符串时使用 SkillLoaderTool 默认值
    """
    agents_cfg = _load_yaml(_CONFIG_DIR / "agents.yaml")
    tasks_cfg = _load_yaml(_CONFIG_DIR / "tasks.yaml")

    orchestrator_cfg: dict = dict(agents_cfg["orchestrator"])

    # 构建工具列表
    tools: list = list(extra_tools or [])
    try:
        from xiaopaw.tools.skill_loader import SkillLoaderTool  # noqa: PLC0415

        loader_kwargs: dict = {"session_id": session_id}
        if history_all is not None:
            loader_kwargs["history_all"] = history_all
        if sandbox_url:
            loader_kwargs["sandbox_url"] = sandbox_url
        tools.append(SkillLoaderTool(**loader_kwargs))
    except ImportError:
        logger.warning("SkillLoaderTool not available, running without it")
    tools.append(IntermediateTool())

    orchestrator = Agent(
        **orchestrator_cfg,
        llm=AliyunLLM(model="qwen3-max", region="cn", temperature=0.3),
        tools=tools,
        verbose=True,
    )

    task_cfg: dict = dict(tasks_cfg["main_task"])
    main_task = Task(
        **task_cfg,
        agent=orchestrator,
        output_pydantic=MainTaskOutput,
    )

    return Crew(
        agents=[orchestrator],
        tasks=[main_task],
        process=Process.sequential,
        verbose=True,
        step_callback=step_callback,
    )


# ── Public factory ────────────────────────────────────────────


def build_agent_fn(
    sender: SenderProtocol,
    max_history_turns: int = _DEFAULT_MAX_HISTORY_TURNS,
    sandbox_url: str = "",
) -> AgentFn:
    """工厂：返回 Runner 可用的 agent_fn 闭包。

    Args:
        sender: 用于 verbose 模式推送推理过程的 Feishu Sender
        max_history_turns: 注入 task 的最大历史条数，超出部分由 history_reader Skill 查询
        sandbox_url: AIO-Sandbox MCP 端点 URL（空字符串时使用默认值）

    Returns:
        agent_fn(user_message, history, session_id, routing_key, root_id, verbose) → str
    """

    async def agent_fn(
        user_message: str,
        history: list[MessageEntry],
        session_id: str,
        routing_key: str = "",
        root_id: str = "",
        verbose: bool = False,
    ) -> str:
        step_cb = (
            _make_step_callback(sender, routing_key, root_id) if verbose else None
        )
        crew = _build_crew(
            session_id=session_id,
            history_all=history,
            step_callback=step_cb,
            sandbox_url=sandbox_url,
        )

        result = await crew.akickoff(
            inputs={
                "user_message": user_message,
                "history": _format_history(history, max_turns=max_history_turns),
            }
        )

        if result.pydantic and hasattr(result.pydantic, "reply"):
            return str(result.pydantic.reply)
        return result.raw or str(result)

    return agent_fn
