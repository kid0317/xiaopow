"""SkillLoaderTool 单元测试"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xiaopaw.tools.skill_loader import SkillLoaderInput, SkillLoaderTool


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_skills_dir(tmp_path: Path) -> Path:
    """在临时目录下创建一个标准 skills 目录结构。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # load_skills.yaml（无前导换行，直接从 key 开始）
    (skills_dir / "load_skills.yaml").write_text(
        "skills:\n"
        "  - name: ref_skill\n"
        "    type: reference\n"
        "    enabled: true\n"
        "  - name: task_skill\n"
        "    type: task\n"
        "    enabled: true\n"
        "  - name: disabled_skill\n"
        "    type: task\n"
        "    enabled: false\n",
        encoding="utf-8",
    )

    # ref_skill SKILL.md（frontmatter 必须从第一行 --- 开始）
    (skills_dir / "ref_skill").mkdir()
    (skills_dir / "ref_skill" / "SKILL.md").write_text(
        "---\n"
        "name: ref_skill\n"
        "description: 参考型 Skill，用于直接读取规范\n"
        "type: reference\n"
        'version: "1.0"\n'
        "---\n"
        "# Ref Skill 操作指南\n\n"
        "这是参考内容，直接返回给 Agent。\n",
        encoding="utf-8",
    )

    # task_skill SKILL.md
    (skills_dir / "task_skill").mkdir()
    (skills_dir / "task_skill" / "SKILL.md").write_text(
        "---\n"
        "name: task_skill\n"
        "description: 任务型 Skill，触发 Sub-Crew 在沙盒执行\n"
        "type: task\n"
        'version: "1.0"\n'
        "---\n"
        "# Task Skill 操作规范\n\n"
        "执行步骤：\n"
        "1. 读取输入文件\n"
        "2. 处理数据\n"
        "3. 写入输出\n",
        encoding="utf-8",
    )

    return skills_dir


def _make_tool(tmp_skills_dir: Path, session_id: str = "sid-test") -> SkillLoaderTool:
    """构建一个指向临时 skills 目录的 SkillLoaderTool。"""
    with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", tmp_skills_dir):
        tool = SkillLoaderTool(session_id=session_id)
    return tool


# ── SkillLoaderInput validator ─────────────────────────────────────────────────


class TestSkillLoaderInput:
    def test_str_passthrough(self):
        inp = SkillLoaderInput(skill_name="foo", task_context="描述文字")
        assert inp.task_context == "描述文字"

    def test_dict_converted_to_json_str(self):
        inp = SkillLoaderInput(skill_name="foo", task_context={"key": "value"})
        assert '"key"' in inp.task_context
        assert '"value"' in inp.task_context

    def test_list_converted_to_json_str(self):
        inp = SkillLoaderInput(skill_name="foo", task_context=[1, 2, 3])
        assert "[1, 2, 3]" in inp.task_context

    def test_none_becomes_empty_str(self):
        inp = SkillLoaderInput(skill_name="foo", task_context=None)
        assert inp.task_context == ""

    def test_default_empty(self):
        inp = SkillLoaderInput(skill_name="foo")
        assert inp.task_context == ""


# ── SkillLoaderTool._build_description ────────────────────────────────────────


class TestBuildDescription:
    def test_registry_populated(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        assert "ref_skill" in tool._skill_registry
        assert "task_skill" in tool._skill_registry
        # disabled_skill 不应在 registry 中
        assert "disabled_skill" not in tool._skill_registry

    def test_description_contains_xml_skills(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        assert "<available_skills>" in tool.description
        assert "<name>ref_skill</name>" in tool.description
        assert "<name>task_skill</name>" in tool.description
        assert "disabled_skill" not in tool.description

    def test_description_contains_session_path(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir, session_id="my-session-id")
        assert "/workspace/sessions/my-session-id/" in tool.description
        assert "uploads/" in tool.description
        assert "outputs/" in tool.description

    def test_missing_manifest_graceful(self, tmp_path: Path):
        empty_dir = tmp_path / "empty_skills"
        empty_dir.mkdir()
        with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", empty_dir):
            tool = SkillLoaderTool(session_id="x")
        assert "未找到 load_skills.yaml" in tool.description

    def test_missing_skill_md_skipped(self, tmp_skills_dir: Path):
        """某个 Skill 目录缺少 SKILL.md 时，跳过而不崩溃。"""
        (tmp_skills_dir / "broken_skill").mkdir()
        (tmp_skills_dir / "load_skills.yaml").write_text(
            "skills:\n  - name: broken_skill\n    type: task\n    enabled: true\n"
        )
        with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", tmp_skills_dir):
            tool = SkillLoaderTool(session_id="x")
        assert "broken_skill" not in tool._skill_registry

    def test_type_shown_in_xml(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        assert "<type>reference</type>" in tool.description
        assert "<type>task</type>" in tool.description


# ── _extract_frontmatter_description ─────────────────────────────────────────


class TestExtractFrontmatterDescription:
    """直接测试 _extract_frontmatter_description，通过一个空 skills 目录避免磁盘依赖。"""

    @pytest.fixture
    def tool(self, tmp_path: Path) -> SkillLoaderTool:
        """构建一个空 registry 的 SkillLoaderTool（空 load_skills.yaml）。"""
        empty_dir = tmp_path / "empty_skills"
        empty_dir.mkdir()
        (empty_dir / "load_skills.yaml").write_text("skills: []\n")
        with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", empty_dir):
            return SkillLoaderTool(session_id="")

    def test_normal_extraction(self, tool: SkillLoaderTool):
        md = "---\ndescription: 这是描述\n---\n正文"
        assert tool._extract_frontmatter_description(md) == "这是描述"

    def test_long_description_truncated(self, tool: SkillLoaderTool):
        long_desc = "A" * 300
        md = f"---\ndescription: {long_desc}\n---\n正文"
        result = tool._extract_frontmatter_description(md)
        assert len(result) <= 203  # 200 chars + "..."
        assert result.endswith("...")

    def test_no_frontmatter_returns_empty(self, tool: SkillLoaderTool):
        md = "# 正文内容\n没有 frontmatter"
        assert tool._extract_frontmatter_description(md) == ""

    def test_missing_description_key_returns_empty(self, tool: SkillLoaderTool):
        md = "---\nname: foo\ntype: task\n---\n正文"
        assert tool._extract_frontmatter_description(md) == ""


# ── _get_skill_instructions ────────────────────────────────────────────────────


class TestGetSkillInstructions:
    def test_frontmatter_stripped(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        instructions = tool._get_skill_instructions("ref_skill")
        assert "---" not in instructions.split("<sandbox_execution_directive>")[0]
        assert "Ref Skill 操作指南" in instructions

    def test_sandbox_directive_appended(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir, session_id="sess-123")
        instructions = tool._get_skill_instructions("task_skill")
        assert "<sandbox_execution_directive>" in instructions
        assert "/workspace/sessions/sess-123/" in instructions
        assert "/mnt/skills/task_skill/" in instructions

    def test_result_cached(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        r1 = tool._get_skill_instructions("ref_skill")
        r2 = tool._get_skill_instructions("ref_skill")
        assert r1 is r2  # 同一对象（缓存命中）


# ── _run / _arun dispatch ─────────────────────────────────────────────────────


class TestRunDispatch:
    def test_run_unknown_skill_returns_error(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        result = tool._run(skill_name="nonexistent", task_context="")
        assert "未找到 Skill" in result
        assert "nonexistent" in result

    @pytest.mark.asyncio
    async def test_arun_unknown_skill_returns_error(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        result = await tool._arun(skill_name="nonexistent", task_context="")
        assert "未找到 Skill" in result

    @pytest.mark.asyncio
    async def test_arun_reference_skill_returns_instructions(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        result = await tool._arun(skill_name="ref_skill", task_context="")
        assert "<skill_instructions>" in result
        assert "Ref Skill 操作指南" in result

    @pytest.mark.asyncio
    async def test_arun_task_skill_calls_build_crew(self, tmp_skills_dir: Path):
        """任务型 Skill 应调用 build_skill_crew 并 await akickoff。"""
        mock_crew = MagicMock()
        mock_crew.akickoff = AsyncMock(return_value="task completed")

        with (
            patch("xiaopaw.tools.skill_loader._SKILLS_DIR", tmp_skills_dir),
            patch(
                "xiaopaw.tools.skill_loader.build_skill_crew",
                return_value=mock_crew,
            ) as mock_build,
        ):
            tool = SkillLoaderTool(session_id="sid-xxx")
            result = await tool._arun(skill_name="task_skill", task_context="do it")

        mock_build.assert_called_once()
        call_kwargs = mock_build.call_args
        assert call_kwargs.kwargs["skill_name"] == "task_skill"
        assert call_kwargs.kwargs["session_id"] == "sid-xxx"
        mock_crew.akickoff.assert_awaited_once()
        assert "task completed" in result

    def test_run_reference_skill_sync_path(self, tmp_skills_dir: Path):
        """同步 _run() 应通过 ThreadPoolExecutor 正确执行参考型 Skill。"""
        tool = _make_tool(tmp_skills_dir)
        result = tool._run(skill_name="ref_skill", task_context="")
        assert "skill_instructions" in result
        assert "Ref Skill 操作指南" in result
