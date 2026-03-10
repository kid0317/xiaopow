"""Sub-Crew 工厂 — XiaoPaw 任务型 Skill 执行层

💡【第03课·上下文隔离】Sub-Crew 是 XiaoPaw 实现 Multi-Agent 上下文隔离的核心机制：
   - 主 Crew 的历史对话不传入 Sub-Crew（Sub-Crew 只看到当前任务指令）
   - Sub-Crew 的执行过程不污染主 Crew（主 Crew 只看到 Sub-Crew 的摘要输出）
   - 这就是课程中"Agent 的数字化职能部门"理念的工程实现：
     主 Agent = PMO（项目管理），Sub-Agent = 各职能部门执行具体工作

💡【第14课·MCP 协议】Sub-Crew 通过 MCPServerHTTP 接入 AIO-Sandbox，
   开放全部 MCP 工具（无白名单过滤）

每次 SkillLoaderTool 触发任务型 Skill 时，调用 build_skill_crew() 构建
一个全新的 Sub-Crew 实例，在 AIO-Sandbox 中执行 Skill 逻辑。

设计要点：
- 每次调用返回新实例，防止 CrewAI 内部状态污染
- Sub-Crew 不注入 step_callback（verbose 只推主 Agent 的推理，避免话题噪音）
- session 工作目录和用户 routing_key 通过 SkillLoaderTool 的 sandbox_execution_directive 注入，不经过主 LLM
"""

from __future__ import annotations

import logging

from crewai import Agent, Crew, Process, Task
from crewai.mcp import MCPServerHTTP

from xiaopaw.llm.aliyun_llm import AliyunLLM

logger = logging.getLogger(__name__)

# ── AIO-Sandbox MCP 配置 ────────────────────────────────────────────────────

# 默认端口：sandbox-docker-compose.yaml 映射 8022:8080
_DEFAULT_SANDBOX_MCP_URL = "http://localhost:8022/mcp"


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
    # 💡【第14课·MCP 接入】MCPServerHTTP 是 CrewAI 原生 MCP 接入方式
    # framework 自动将 MCP 工具转换为 Agent 可用工具，无需手动封装
    # 💡【第03课·工厂模式】每次构建新实例 → 新的 MCP 连接 → 状态完全隔离
    sandbox_mcp = MCPServerHTTP(
        url=sandbox_mcp_url,
    )

    skill_llm = AliyunLLM(model=sub_agent_model, region="cn", temperature=0.3)

    session_dir = (
        f"/workspace/sessions/{session_id}" if session_id else "/workspace/sessions/<session_id>"
    )

    # 💡【第07课·Agent 三要素动态构建】Sub-Crew 的 role/goal/backstory 不来自 YAML，
    # 而是根据 skill_name 和 session 工作目录动态生成——这是 Sub-Crew 与主 Crew 的关键区别：
    # 主 Crew 人设固定（YAML），Sub-Crew 人设按任务动态定制
    # 💡【第07课·max_iter】Sub-Agent 迭代上限设为 20（默认），比主 Agent（50）更保守，
    # 因为 Sub-Task 范围明确，迭代超限通常意味着 Skill 指令有问题
    skill_agent = Agent(
        role=f"{skill_name.upper()} Skill 执行专家",
        goal=f"严格按照 {skill_name} Skill 的操作规范，在 AIO-Sandbox 中完成任务",
        backstory=(
            f"你是一位专精于 {skill_name} 处理的 AI 专家。\n"
            f"当前 Session 沙盒工作目录：{session_dir}/\n"
            f"  - 用户上传的文件位于：{session_dir}/uploads/\n"
            f"  - 任务输出文件写入：{session_dir}/outputs/\n"
            f"  - 临时工作区：{session_dir}/tmp/\n\n"
            f"⚠️ 工具使用规范（必须严格遵守）：\n"
            f"你没有名为 '{skill_name}' 的直接工具。\n"
            f"所有操作必须通过沙盒工具完成：\n"
            f"  - sandbox_execute_bash：运行 bash 命令或 Python 脚本\n"
            f"  - sandbox_execute_code：在沙盒中直接执行 Python 代码\n"
            f"  - sandbox_file_operations：读写沙盒文件\n"
            f"  - sandbox_str_replace_editor：在沙盒中编辑文件\n"
            f"  - sandbox_convert_to_markdown：将 URL 转换为 Markdown\n"
            f"  - browser_* 系列工具：浏览器自动化\n"
            f"绝对禁止：将 '{skill_name}' 作为工具名调用（该名称不是任何工具）。\n\n"
            f"你掌握以下操作规范，请严格遵循：\n\n"
            f"{skill_instructions}"
        ),
        llm=skill_llm,
        # 💡【第14课·MCP 接入】mcps 参数接收 MCPServerHTTP 列表，框架自动管理连接
        mcps=[sandbox_mcp],
        verbose=True,
        max_iter=max_iter,
    )

    # 💡【第08课·Task 契约】description 明确执行环境约束，expected_output 定义 JSON 格式
    # 注意：{task_context} 由 akickoff(inputs=...) 显式注入（第09课·显式上下文传递）
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

    # 💡【第09课·Sequential Process】Sub-Crew 同样使用顺序执行
    # 单 Agent 单 Task 天然顺序，此处显式声明让代码意图清晰
    # 注意：Sub-Crew 不传入 step_callback——Sub-Crew 的推理过程不推送到飞书，
    # 避免在 verbose 模式下产生对用户来说难以理解的底层执行噪音
    return Crew(
        agents=[skill_agent],
        tasks=[skill_task],
        process=Process.sequential,
        verbose=True,
    )
