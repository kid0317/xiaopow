"""Sub-Crew 工厂 — XiaoPaw 任务型 Skill 执行层

每次 SkillLoaderTool 触发任务型 Skill 时，调用 build_skill_crew() 构建
一个全新的 Sub-Crew 实例，在 AIO-Sandbox 中执行 Skill 逻辑。

设计要点：
- 每次调用返回新实例，防止 CrewAI 内部状态污染
- Sub-Crew 不注入 step_callback（verbose 只推主 Agent 的推理）
- session_id 通过 task.description 注入，Agent 知道自己的沙盒工作目录
- 工具白名单：只开放 4 个沙盒工具，防止越权
"""

from __future__ import annotations

import logging

from crewai import Agent, Crew, Process, Task
from crewai.mcp import MCPServerHTTP
from crewai.mcp.filters import create_static_tool_filter

from xiaopaw.llm.aliyun_llm import AliyunLLM

logger = logging.getLogger(__name__)

# ── AIO-Sandbox MCP 配置 ────────────────────────────────────────────────────

# 默认端口：sandbox-docker-compose.yaml 映射 8022:8080
_DEFAULT_SANDBOX_MCP_URL = "http://localhost:8022/mcp"

# 白名单过滤：只开放 4 个沙盒工具，排除 browser_* 系列
_SANDBOX_TOOL_FILTER = create_static_tool_filter(
    allowed_tool_names=[
        "sandbox_execute_bash",
        "sandbox_execute_code",
        "sandbox_file_operations",
        "sandbox_str_replace_editor",
    ]
)


def build_skill_crew(
    skill_name: str,
    skill_instructions: str,
    session_id: str = "",
    sandbox_mcp_url: str = _DEFAULT_SANDBOX_MCP_URL,
    sub_agent_model: str = "qwen3-max",
    max_iter: int = 20,
) -> Crew:
    """
    Sub-Crew 工厂：为指定 Skill 构建一个在 AIO-Sandbox 中执行的独立 Crew。

    Args:
        skill_name: Skill 名称，用于 Agent role 和日志
        skill_instructions: 完整 SKILL.md 正文（已剥离 frontmatter + 注入沙盒路径指令）
        session_id: 当前会话 ID，Agent 使用此 ID 确定沙盒工作目录
        sandbox_mcp_url: AIO-Sandbox MCP 端点 URL
        sub_agent_model: Sub-Agent 使用的 LLM 模型名
        max_iter: Sub-Agent 最大迭代次数，防止无限循环

    Returns:
        已配置好的 Crew 实例，可直接调用 kickoff() / akickoff()
    """
    # 💡 核心点：每次构建新实例，MCP 连接独立，防止状态污染
    sandbox_mcp = MCPServerHTTP(
        url=sandbox_mcp_url,
        tool_filter=_SANDBOX_TOOL_FILTER,
    )

    skill_llm = AliyunLLM(model=sub_agent_model, region="cn", temperature=0.3)

    session_dir = (
        f"/workspace/sessions/{session_id}" if session_id else "/workspace/sessions/<session_id>"
    )

    skill_agent = Agent(
        role=f"{skill_name.upper()} Skill 执行专家",
        goal=f"严格按照 {skill_name} Skill 的操作规范，在 AIO-Sandbox 中完成任务",
        backstory=(
            f"你是一位专精于 {skill_name} 处理的 AI 专家。\n"
            f"当前 Session 沙盒工作目录：{session_dir}/\n"
            f"  - 用户上传的文件位于：{session_dir}/uploads/\n"
            f"  - 任务输出文件写入：{session_dir}/outputs/\n"
            f"  - 临时工作区：{session_dir}/tmp/\n\n"
            f"你掌握以下操作规范，请严格遵循：\n\n"
            f"{skill_instructions}"
        ),
        llm=skill_llm,
        mcps=[sandbox_mcp],
        verbose=True,
        max_iter=max_iter,
    )

    skill_task = Task(
        description=(
            "根据以下任务要求，使用你掌握的 Skill 操作规范完成任务。\n\n"
            "任务要求：\n{task_context}\n\n"
            "执行约束：\n"
            "1. 所有操作必须在 AIO-Sandbox 中执行，禁止直接操作本地文件系统\n"
            f"2. 输入文件从沙盒路径 {session_dir}/uploads/ 读取\n"
            f"3. 输出文件必须写到沙盒路径 {session_dir}/outputs/ 目录下\n"
            "4. 如遇依赖缺失，先在沙盒中 pip install 再继续\n"
            "5. 返回结果必须符合 task_context 中定义的 JSON schema"
        ),
        expected_output=(
            "一份结构化的任务执行结果 JSON，包含 errcode（0=成功）、"
            "errmsg（成功时固定'success'，失败时含错误原因和建议）、"
            "以及 task_context 中定义的其余字段。"
        ),
        agent=skill_agent,
    )

    return Crew(
        agents=[skill_agent],
        tasks=[skill_task],
        process=Process.sequential,
        verbose=True,
    )
