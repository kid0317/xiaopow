"""SkillLoaderTool — XiaoPaw 核心工具

设计要点：
  1. 渐进式披露（Progressive Disclosure）
     - __init__ 只解析 SKILL.md 的 YAML frontmatter，构建轻量 XML 注入工具 description
     - 主 Agent 通过 description 感知"有哪些 Skill、各自用途"
     - 真正调用时才读取完整 SKILL.md 正文（按需加载）

  2. 参考型 vs 任务型
     - reference：返回指令文本，主 Agent 自行消化，不启动 Sub-Crew
     - task：触发独立 Sub-Crew + AIO-Sandbox 执行，上下文完全隔离

  3. 异步双通道
     - _arun()：FastAPI akickoff() 调用链的主路径，原生 await
     - _run()：同步 fallback，ThreadPoolExecutor 提供独立 event loop，
               规避 "cannot run nested event loop" 错误

  4. 会话隔离
     - 每个 SkillLoaderTool 实例绑定 session_id
     - Sub-Crew 的工作目录限定在 /workspace/sessions/{session_id}/
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import re
from pathlib import Path
from typing import Any, Union

import yaml
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr, field_validator

from xiaopaw.agents.skill_crew import build_skill_crew

logger = logging.getLogger(__name__)

# ── 路径常量 ────────────────────────────────────────────────────────────────

# SKILLS_DIR：本项目 skills 目录
_SKILLS_DIR = Path(__file__).parents[2] / "xiaopaw" / "skills"

# 沙盒内的 skills 挂载路径（与 sandbox-docker-compose.yaml volumes 对应）
_SANDBOX_SKILLS_MOUNT = "/mnt/skills"


# ── 输入 Schema ─────────────────────────────────────────────────────────────


class SkillLoaderInput(BaseModel):
    skill_name: str = Field(
        description="要加载的 Skill 名称，必须严格来自工具描述 XML 列表中的 <name> 值"
    )
    task_context: str = Field(
        default="",
        description=(
            "如果是参考型skill，此项为空。\n"
            "如果是任务型skill，此项为调用此 Skill 要完成的子任务的完整描述（必须是字符串）。"
            "可写自然语言描述，或 JSON 字符串。若传入对象会自动转为 JSON 字符串。包括：\n"
            "1. 子任务的概要描述\n"
            "2. 任务完成目标的预期输出，这里必须是结构化格式，通过一个json schema进行定义。"
            "各字段必须有明确描述和示例。有两个必选字段errcode和errmsg：errcode为0表示成功，"
            "非0表示失败；errmsg成功时固定返回\"success\"，失败时必须包括错误信息、错误原因和建议的下一步解决方案。\n"
            "3. （可选）如果有完成任务的参考步骤和方法，可以提供对应描述\n"
            "4. （可选）输入文件请使用沙盒绝对路径（路径来自工具描述中的当前 session 工作目录）\n"
            "5. （可选）输出文件请写到 session 工作目录下的 outputs/ 目录\n"
            "6. （可选）如有其它特殊要求，可在此处提供\n"
            "提供信息越完整，Skill 执行越精准。"
        ),
    )

    @field_validator("task_context", mode="before")
    @classmethod
    def task_context_to_str(cls, v: Union[str, dict, list, None]) -> str:
        """LLM 常传 dict/list，此处统一转为字符串，避免 Pydantic string_type 校验失败。"""
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)


# ── 核心工具 ─────────────────────────────────────────────────────────────────


class SkillLoaderTool(BaseTool):
    """渐进式 Skill 加载工具，是主 Agent 的唯一能力扩展入口。"""

    name: str = "skill_loader"
    description: str = ""  # 在 __init__ 中动态构建
    args_schema: type[BaseModel] = SkillLoaderInput

    # Pydantic 会把普通 dict 属性当作模型字段，用 PrivateAttr 绕开
    _session_id: str = PrivateAttr(default="")
    _sandbox_url: str = PrivateAttr(default="")
    _skill_registry: dict[str, Any] = PrivateAttr(default_factory=dict)
    _instruction_cache: dict[str, str] = PrivateAttr(default_factory=dict)
    _history_all: list = PrivateAttr(default_factory=list)

    def __init__(self, session_id: str = "", sandbox_url: str = "", history_all: list | None = None) -> None:
        super().__init__()
        self._session_id = session_id
        # sandbox_url 透传给 build_skill_crew；空字符串时使用 skill_crew 模块的默认值
        self._sandbox_url = sandbox_url
        self._history_all = list(history_all) if history_all else []
        self._skill_registry = {}
        self._instruction_cache = {}
        self._build_description()

    # ── 阶段 1：元数据解析，构建 XML description ────────────────────────────

    def _build_description(self) -> None:
        """
        💡 核心点：渐进式披露第一阶段
        只读 frontmatter，构建轻量 XML 注入 description。
        主 Agent 看到工具 → 知道"什么场景用什么 Skill"，但不加载完整指令。
        """
        manifest_path = _SKILLS_DIR / "load_skills.yaml"
        if not manifest_path.exists():
            self.description = (
                "SkillLoaderTool 已初始化，但未找到 load_skills.yaml，暂无可用 Skill。"
            )
            return

        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = yaml.safe_load(f) or {}
        except Exception as exc:  # noqa: BLE001
            # 日志记录完整错误（含路径），但不暴露给 LLM（防止信息泄漏）
            logger.error("SkillLoaderTool: failed to parse load_skills.yaml", exc_info=True)
            _ = exc  # 已记录，不再使用
            self.description = "SkillLoaderTool 初始化失败：配置解析错误，请联系管理员检查 load_skills.yaml。"
            return

        skills_conf = manifest.get("skills") or []
        _skills_root = _SKILLS_DIR.resolve()

        xml_parts = ["<available_skills>"]
        for skill_conf in skills_conf:
            if not skill_conf.get("enabled", True):
                continue
            name = skill_conf["name"]
            skill_type = skill_conf.get("type", "task")

            # 💡 安全约束：防止路径穿越（如 name="../../etc"）
            skill_path = (_SKILLS_DIR / name).resolve()
            if not str(skill_path).startswith(str(_skills_root)):
                logger.warning(
                    "SkillLoaderTool: blocked path traversal attempt, skill name=%r", name
                )
                continue

            skill_md_path = skill_path / "SKILL.md"
            if not skill_md_path.exists():
                logger.warning("SKILL.md not found for skill: %s, skipping", name)
                continue

            skill_md = skill_md_path.read_text(encoding="utf-8")
            desc = self._extract_frontmatter_description(skill_md)

            self._skill_registry[name] = {
                "type": skill_type,
                "path": skill_path,
            }
            xml_parts.append(
                f"  <skill>\n"
                f"    <name>{name}</name>\n"
                f"    <type>{skill_type}</type>\n"
                f"    <description>{desc}</description>\n"
                f"  </skill>"
            )
        xml_parts.append("</available_skills>")

        session_dir = f"/workspace/sessions/{self._session_id}" if self._session_id else "/workspace/sessions/<session_id>"
        self.description = (
            "当需要完成的任务涉及以下XML列表中的技能时，调用此工具。\n"
            "根据下方 XML 列表选择正确的 skill_name，并在 task_context 中提供完整任务信息。\n"
            f"当前 session 工作目录（沙盒路径）：{session_dir}/\n"
            f"  - 输入文件（用户上传）：{session_dir}/uploads/\n"
            f"  - 输出文件（Skill 产出）：{session_dir}/outputs/\n\n"
            + "\n".join(xml_parts)
        )

    def _extract_frontmatter_description(self, content: str) -> str:
        """从 SKILL.md 的 YAML frontmatter 中提取 description 字段（最多 200 字符）"""
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return ""
        try:
            front = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return ""
        desc = front.get("description", "") if front else ""
        if not desc:
            return ""
        return desc[:200] + "..." if len(desc) > 200 else desc

    # ── 阶段 2：按需加载完整指令 ─────────────────────────────────────────────

    def _get_skill_instructions(self, skill_name: str) -> str:
        """
        💡 核心点：渐进式披露第二阶段
        读取完整 SKILL.md，剥离 frontmatter，拼接沙盒路径替换指令。
        结果写入 _instruction_cache，同一 Skill 只读一次文件。
        """
        if skill_name in self._instruction_cache:
            return self._instruction_cache[skill_name]

        skill_path = self._skill_registry[skill_name]["path"]
        content = (skill_path / "SKILL.md").read_text(encoding="utf-8")
        # 剥离 YAML frontmatter（--- ... ---）
        stripped = re.sub(r"^---\n.*?\n---\n?", "", content, flags=re.DOTALL)

        # 沙盒路径常量
        _skill_base = f"{_SANDBOX_SKILLS_MOUNT}/{skill_name}"
        _session_dir = f"/workspace/sessions/{self._session_id}" if self._session_id else "/workspace/sessions/<session_id>"

        # 拼接沙盒路径替换指令，消灭 LLM 路径幻觉
        sandbox_directive = (
            f"\n\n<sandbox_execution_directive>\n"
            f"IMPORTANT:【强制约束】所有脚本和文件操作必须在 AIO-Sandbox 中执行，禁止直接操作本地文件系统。\n"
            f"Skill 资源已挂载至沙盒：{_skill_base}/\n"
            f"当前 Session 工作目录（沙盒）：{_session_dir}/\n"
            f"  - 用户上传文件：{_session_dir}/uploads/（只读访问）\n"
            f"  - 输出文件目录：{_session_dir}/outputs/（读写，所有输出文件写在此处）\n"
            f"  - 临时文件目录：{_session_dir}/tmp/（临时工作区）\n\n"
            f"可用沙盒工具及正确用法：\n"
            f"1. sandbox_execute_bash：执行 Shell 命令。参数：cmd（必填）、cwd（可选）、timeout（可选，秒）。\n"
            f"   - 运行脚本示例：cmd=\"python {_skill_base}/scripts/xxx.py 参数\"\n"
            f"   - 安装依赖：cmd=\"pip install 包名\"，再重试任务。\n"
            f"2. sandbox_file_operations：统一文件操作。action='read'|'write'|'list'|'find'|'replace'|'search'。\n"
            f"   - 读取文件：action=\"read\", path=\"文件绝对路径\"\n"
            f"   - 列出目录：action=\"list\", path=\"{_session_dir}/uploads\"\n"
            f"3. sandbox_str_replace_editor：编辑文件。command='view'|'create'|'str_replace'|'insert'。\n"
            f"4. sandbox_execute_code：执行代码片段。language='python'|'javascript'。\n"
            f"</sandbox_execution_directive>"
        )

        result = stripped + sandbox_directive
        self._instruction_cache[skill_name] = result
        return result

    # ── Sub-Crew 执行（任务型 Skill）────────────────────────────────────────

    def _handle_history_reader(self, task_context: str) -> str:
        """内联处理 history_reader：从 _history_all 分页读取，无需沙盒或 session_id。

        Args:
            task_context: JSON 字符串，支持 page（页码，从1开始）和 page_size（每页条数）

        Returns:
            SkillResult JSON 字符串
        """
        try:
            params = json.loads(task_context) if task_context.strip().startswith("{") else {}
        except (json.JSONDecodeError, Exception):
            params = {}

        page = max(1, int(params.get("page", 1)))
        page_size = max(1, min(50, int(params.get("page_size", 20))))

        all_msgs = self._history_all
        total = len(all_msgs)
        total_pages = max(1, (total + page_size - 1) // page_size)

        start = (page - 1) * page_size
        end = start + page_size
        page_msgs = all_msgs[start:end]

        messages = [
            {"role": m.role, "content": m.content}
            for m in page_msgs
        ]

        result = {
            "errcode": 0,
            "message": f"成功读取第 {page} 页，共 {total} 条消息，本页 {len(messages)} 条",
            "data": {
                "messages": messages,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
            },
        }
        return json.dumps(result, ensure_ascii=False)

    async def _execute_skill_async(self, skill_name: str, task_context: str) -> str:
        """核心执行路径：加载指令，按 type 分流。"""
        # 💡 安全设计：history_reader 内联处理，从系统维护的 _history_all 读取，
        # 不依赖沙盒、不暴露 session_id 给 LLM
        if skill_name == "history_reader":
            return self._handle_history_reader(task_context)
        skill_info = self._skill_registry[skill_name]
        instructions = self._get_skill_instructions(skill_name)

        if skill_info["type"] == "reference":
            # 参考型：直接返回指令文本，不启动 Sub-Crew
            return f"<skill_instructions>\n{instructions}\n</skill_instructions>"

        # 任务型：启动独立 Sub-Crew，在沙盒中执行
        # 💡 核心点：每次 build_skill_crew() 返回新实例，防止状态污染
        crew_kwargs: dict = {
            "skill_name": skill_name,
            "skill_instructions": instructions,
            "session_id": self._session_id,
        }
        if self._sandbox_url:
            crew_kwargs["sandbox_mcp_url"] = self._sandbox_url
        crew = build_skill_crew(**crew_kwargs)
        result = await crew.akickoff(
            inputs={
                "task_context": task_context,
                "skill_name": skill_name,
            }
        )
        return str(result)

    # ── 异步路径（FastAPI / akickoff 调用链）────────────────────────────────

    async def _arun(self, skill_name: str, task_context: str = "") -> str:
        """
        💡 核心点：FastAPI 异步调用链的主路径，直接 await Sub-Crew
        CrewAI 在 arun() 内部调用 _arun()，框架自动选路
        """
        if skill_name not in self._skill_registry:
            available = list(self._skill_registry.keys())
            return (
                f"错误：未找到 Skill '{skill_name}'。\n"
                f"可用 Skill：{available}\n"
                f"请从以上列表中选择正确的 skill_name 重新调用。"
            )
        return await self._execute_skill_async(skill_name, task_context)

    # ── 同步路径（脚本 / 测试场景 fallback）─────────────────────────────────

    def _run(self, skill_name: str, task_context: str = "") -> str:
        """
        💡 核心点：用 ThreadPoolExecutor 在新线程中运行独立 event loop，
        规避主线程已有 event loop 时 asyncio.run() 报
        'cannot run nested event loop' 的问题
        """
        if skill_name not in self._skill_registry:
            available = list(self._skill_registry.keys())
            return (
                f"错误：未找到 Skill '{skill_name}'。\n"
                f"可用 Skill：{available}\n"
                f"请从以上列表中选择正确的 skill_name 重新调用。"
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                self._execute_skill_async(skill_name, task_context),
            )
            return future.result()
