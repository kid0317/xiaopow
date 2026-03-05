# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**XiaoPaw (小爪子)** is a Feishu (Lark) local work assistant that uses a Skills ecosystem to give an AI agent extensible tool capabilities, with all execution isolated in AIO-Sandbox (Docker). It connects via Feishu WebSocket (no public IP needed), making it suitable for local/intranet deployment.

The design document is in `DESIGN.md` (Chinese, compressed overview). Detailed sub-module docs are in `docs/`:
- `docs/design-modules.md` — §4 模块设计（FeishuListener, Runner, Main Agent, Sub-Crew, CronService, TestAPI 等）
- `docs/design-data.md` — §5 数据设计（Session 存储、Trace、CronJob、Skill 定义、SkillLoaderTool I/O）
- `docs/design-api.md` — §6 接口设计（飞书消息收发、文件下载接口）
- `docs/design-observability.md` — §13 可观测性（日志规范、Prometheus 指标、/metrics 接口）

All code and comments should be written in Chinese where user-facing, English for code identifiers.

## Tech Stack

- **Python** (async, asyncio-based)
- **CrewAI** — Agent orchestration (Main Agent + Sub-Crew pattern)
- **lark-oapi** — Feishu SDK (WebSocket client + REST API)
- **Qwen3-max** — LLM model for agents
- **AIO-Sandbox** — Docker-based MCP server for isolated code execution
- **croniter** — Cron expression parsing for scheduled tasks
 - **prometheus_client** — Metrics export for observability
 - **AliyunLLM adapter** — Custom CrewAI `BaseLLM` implementation in `xiaopaw/llm/aliyun_llm.py` for calling Qwen via DashScope-compatible API (supports retries, function calling, multimodal image inputs)

## Architecture (Key Concepts)

**Message flow**: Feishu WebSocket → FeishuListener → SessionRouter (routing_key) → Runner → Main Agent → SkillLoaderTool → Sub-Crew (sandbox) → FeishuSender

**Three routing key types**: `p2p:{open_id}` (DM), `group:{chat_id}` (group chat), `thread:{chat_id}:{thread_id}` (topic thread)

**Two Skill types**:
- **reference** — SKILL.md content returned to Main Agent for self-reasoning
- **task** — Spawns an isolated Sub-Crew with sandbox MCP tools (4-tool whitelist: bash, code, file_ops, editor)

**Main Agent has exactly one tool**: `SkillLoaderTool` (progressive disclosure pattern). All other capabilities come through Skills.

**Credentials never enter the LLM** — written to sandbox `.config/feishu.json` at startup, read directly by Skill scripts.

## Module Layout

```
xiaopaw/
├── main.py                  # Entry: starts Listener + CronService + CleanupService + TestAPI
├── config.yaml              # Workspace config (feishu creds via env vars)
├── llm/
│   └── aliyun_llm.py        # AliyunLLM: CrewAI BaseLLM adapter for Aliyun Qwen
├── feishu/
│   ├── listener.py          # WebSocket event → InboundMessage
│   ├── downloader.py        # File/image download to session uploads/
│   ├── sender.py            # Send messages (create/reply), routing_key-aware
│   └── session_key.py       # routing_key resolution
├── api/
│   └── test_server.py       # Test API (aiohttp): simulate Feishu events, debug mode only
├── runner.py                # Core orchestrator: session → slash cmd → agent → store → send
├── agents/
│   ├── main_crew.py         # Main Crew (single SkillLoaderTool)
│   └── skill_crew.py        # (TODO) Sub-Crew factory (build_skill_crew)
├── tools/
│   ├── skill_loader.py          # (TODO) SkillLoaderTool (progressive disclosure + Sub-Crew trigger)
│   ├── add_image_tool_local.py  # AddImageToolLocal: local image → base64 data URL for multimodal LLM
│   ├── baidu_search_tool.py     # BaiduSearchTool: Baidu Qianfan web_search wrapper
│   └── intermediate_tool.py     # IntermediateTool: save intermediate thinking products
├── observability/
│   ├── logging_config.py        # Logging setup: console + JSON log file in data/logs/xiaopaw.log
│   ├── metrics.py               # Prometheus metrics definitions and helper functions
│   └── metrics_server.py        # Lightweight aiohttp server exposing /metrics for Prometheus
├── session/
│   ├── manager.py           # index.json + JSONL read/write
│   └── models.py            # Session / SessionEntry dataclasses
├── cron/
│   ├── service.py           # asyncio timer + mtime hot-reload
│   └── models.py            # CronJob / CronSchedule / CronPayload
├── cleanup/
│   └── service.py           # (TODO) Storage cleanup by policy
├── skills/                  # SKILL.md + scripts per skill
│   ├── file_processor/      # PDF/DOCX parsing and conversion
│   ├── feishu_ops/          # Read docs, send messages via Feishu API
│   ├── baidu_search/        # Baidu search with summarization
│   ├── scheduler_mgr/       # Create/list/delete cron jobs (config only, not execution)
│   └── history_reader/      # Paginated conversation history reader (reference skill)
└── data/                    # Runtime data (.gitignore)
    ├── sessions/            # index.json + {sid}.jsonl
    ├── traces/              # Full LLM execution traces
    ├── cron/tasks.json      # Scheduled task configs
    └── workspace/           # Per-session file workspace (mounted into sandbox)
```

## Key Design Decisions

- **Slash commands** (`/new`, `/verbose`, `/help`, `/status`) are intercepted in Runner *before* entering the Agent
- **Per-routing_key message queue** — same session messages are processed serially via `asyncio.Queue`; different sessions run in parallel. Worker auto-exits after idle timeout.
- **File concurrency** — `asyncio.Lock` for in-process mutual exclusion; `write-then-rename` for atomic JSON writes; `flush + fsync` for JSONL appends. No cross-process file locks (single-process architecture).
- **CronService** uses asyncio precise timers (not polling), with mtime-based hot-reload of `tasks.json`
- **Sub-Crew instances are ephemeral** — new instance per Skill invocation to prevent state contamination
- **Verbose mode** streams Main Agent's Thought via `step_callback` to Feishu (disabled for thread chats and Sub-Crews)
- **Session workspace** is mounted into Docker: `./data/workspace:/workspace` — Sub-Crew accesses files at `/workspace/sessions/{sid}/`
- **SkillLoaderTool I/O** uses Pydantic models (`SkillLoaderInput` / `SkillResult`) with `errcode=0` for success
- **All timeouts and limits are configurable** in `config.yaml` (agent max_iter, sandbox timeout, queue size, sender retries, etc.)
- **Test API** (`debug.enable_test_api`) — HTTP endpoint simulates Feishu events via `POST /api/test/message`, injects into Runner with a `CaptureSender` that returns bot replies synchronously. Runner accepts a `SenderProtocol` (production: `FeishuSender`, test: `CaptureSender`).

## Data Formats

- **Session index**: `data/sessions/index.json` — maps routing_key → active_session_id + session metadata
- **Conversation history**: `data/sessions/{sid}.jsonl` — clean message log (meta line + user/assistant pairs)
- **Traces**: `data/traces/{sid}/{ts}_{msg_id}/` — meta.json + main.jsonl + skills/*.jsonl
- **Cron jobs**: `data/cron/tasks.json` — three schedule kinds: `at` (one-shot), `every` (interval), `cron` (cron expr)
- **Skill definitions**: `skills/{name}/SKILL.md` with YAML frontmatter (name, description, type, version)

## Sandbox Tool Whitelist

Sub-Crews connect to AIO-Sandbox MCP with exactly 4 allowed tools:
`sandbox_execute_bash`, `sandbox_execute_code`, `sandbox_file_operations`, `sandbox_str_replace_editor`

## Commands

```bash
# Run all tests with coverage (use venv python)
.venv/bin/python -m pytest tests/ -v --cov=xiaopaw --cov-report=term-missing

# Run a single test file
.venv/bin/python -m pytest tests/unit/test_cron_service.py -v

# Run a specific test
.venv/bin/python -m pytest tests/unit/test_runner.py::TestSlashNew::test_creates_new_session -v
```

## Development Progress

**Completed modules** (with tests):
- `xiaopaw/models.py` — InboundMessage, Attachment, SenderProtocol
- `xiaopaw/session/models.py` — SessionEntry, RoutingEntry, MessageEntry
- `xiaopaw/session/manager.py` — SessionManager (index.json + JSONL, concurrent-safe)
- `xiaopaw/api/capture_sender.py` — CaptureSender (future-based reply capture)
- `xiaopaw/api/schemas.py` — TestRequest, TestResponse (Pydantic)
- `xiaopaw/api/test_server.py` — TestAPI (aiohttp, wired with Runner + SessionManager)
- `xiaopaw/runner.py` — Runner (per-routing_key queue, slash commands, agent_fn DI)
- `xiaopaw/feishu/session_key.py` — resolve_routing_key (pure function)
- `xiaopaw/cron/models.py` — CronJob, CronSchedule, CronPayload, CronState
- `xiaopaw/cron/service.py` — CronService (tick-based scheduler, mtime hot-reload)
- `xiaopaw/feishu/listener.py` — FeishuListener wired to WebSocket (im.message.receive_v1 → Runner.dispatch)
- `xiaopaw/feishu/sender.py` — FeishuSender (lark-oapi, p2p/group/thread text send)
- `xiaopaw/main.py` — Minimal entry point: load config.yaml, start Runner + FeishuListener, fixed reply "收到，session={id}"
- `xiaopaw/llm/aliyun_llm.py` — AliyunLLM: CrewAI `BaseLLM` adapter for Qwen (sync/async, retries, function calling, multimodal)
- `xiaopaw/tools/add_image_tool_local.py` — AddImageToolLocal: 本地图片 → base64 data URL，含路径遍历防护
- `xiaopaw/tools/baidu_search_tool.py` — BaiduSearchTool: 百度千帆 web_search 封装
- `xiaopaw/tools/intermediate_tool.py` — IntermediateTool: 中间思考产物保存
- `xiaopaw/observability/metrics.py` — Prometheus metrics 定义与 helper 函数
- `xiaopaw/observability/metrics_server.py` — /metrics aiohttp 服务（含 AppRunner 清理）
- `xiaopaw/feishu/downloader.py` — FeishuDownloader: 附件下载到 workspace/sessions/{sid}/uploads/
- `xiaopaw/agents/main_crew.py` — MainCrew: build_agent_fn() 工厂，YAML prompts，verbose step_callback，历史截断
- `xiaopaw/agents/models.py` — MainTaskOutput Pydantic 输出模型
- `xiaopaw/agents/config/agents.yaml` — Orchestrator Agent 配置（role/goal/backstory/max_iter）
- `xiaopaw/agents/config/tasks.yaml` — 主任务配置（description/expected_output）
- `xiaopaw/skills/history_reader/SKILL.md` — history_reader Skill（分页读取历史对话）

**Test stats**: 253 tests, 88.17% coverage ✅

## Known Issues / Code Quality

Full report: run `/everything-claude-code:python-review` to re-evaluate.

Last review: 2026-03-05. All CRITICAL and HIGH issues fixed. Remaining MEDIUM:

| # | File | Issue | Status |
|---|------|-------|--------|
| M1 | `aliyun_llm.py:110-139` | `_normalize_multimodal_tool_result` 用脆弱字符串匹配检测图片 URL，易被注入破坏 | open |

**Not yet implemented**:
- `agents/skill_crew.py` — Sub-Crew factory
- `tools/skill_loader.py` — SkillLoaderTool
- `cleanup/service.py` — Storage cleanup
