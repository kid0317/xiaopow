## XiaoPaw（小爪子）

本仓库是一个基于飞书的本地工作助手框架，通过 Skills 生态 + AIO-Sandbox（Docker）实现安全可扩展的工具调用。目前已实现：

- 会话与存储层（SessionManager + JSONL 历史）
- per-routing_key 串行队列的 `Runner`
- Cron 调度模型与服务
- 本地测试用 `TestAPI`（HTTP）
- **最小可用链路**：飞书 WebSocket → XiaoPaw → 固定回复“收到”

此外，已有一批核心模块**代码与测试已完成（但尚未在默认入口接线）**，进度详见 `CLAUDE.md` 中的 Development Progress：

- CrewAI 主 Agent `MainCrew` 及其 YAML 配置
- LLM 适配层 `AliyunLLM`（通义千问 / Qwen，多模态 + Function Calling）
- 通用工具：`AddImageToolLocal` / `BaiduSearchTool` / `IntermediateTool`
- 可观测性组件：`metrics` / `metrics_server`
- Feishu 附件下载器 `FeishuDownloader`

### 目录结构（简要）

- `xiaopaw/`
  - `main.py`：进程入口（当前为最小可用版本）
  - `models.py`：`InboundMessage` / `Attachment` / `SenderProtocol`
  - `runner.py`：执行引擎（队列、Slash 命令、Agent 调用占位）
  - `llm/`：LLM 适配层（`AliyunLLM`，封装通义千问 / Qwen 调用，支持 Function Calling 与多模态）
  - `session/`：Session 模型与管理（index.json + JSONL）
  - `cron/`：Cron 数据模型与服务
  - `feishu/`
    - `session_key.py`：routing_key 解析
    - `listener.py`：飞书 WebSocket 监听
    - `sender.py`：飞书消息发送
  - `tools/`：通用工具（如 `AddImageToolLocal`、`BaiduSearchTool`、`IntermediateTool` 等）
  - `observability/`：可观测性模块（日志与 Prometheus Metrics）
  - `api/`：测试用 HTTP Server（TestAPI）
- `tests/`：单元测试与集成测试
- `config.yaml`：运行配置（飞书凭证、数据目录等）

更多设计细节见 `DESIGN.md`。

### 环境准备

- Python 3.11+
- 已创建的飞书自建应用，具备 IM 消息收发与 WebSocket 相关权限
- 推荐使用虚拟环境（如 `python -m venv .venv`）

```bash
python -m venv .venv
source .venv/bin/activate  # Windows 使用 .venv\Scripts\activate
pip install -r requirements.txt  # 国内可以用阿里源：pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
export BAIDU_API_KEY={百度搜索的apikey}
export QWEN_API_KEY={阿里云千问的API Key}

```

> 依赖列表以实际 `requirements.txt` 为准。

### 配置 `config.yaml`

根目录已提供一份 `config.yaml`（可参考同目录下的 `config.yaml.template`），核心字段：

- **飞书配置**
  - `feishu.app_id`: 飞书开放平台应用的 App ID
  - `feishu.app_secret`: 飞书开放平台应用的 App Secret
  - `feishu.encrypt_key`: 可选，事件加密用
  - `feishu.verification_token`: 可选，事件验证用
- **数据目录**
  - `data_dir`: 运行时数据根目录，默认 `./data`

示例（脱敏）：

```yaml
feishu:
  app_id: "${FEISHU_APP_ID}"
  app_secret: "${FEISHU_APP_SECRET}"
  encrypt_key: ""
  verification_token: ""

data_dir: "./data"
```

可以直接填写明文，也可以在外部通过环境变量注入。

### 启动最小链路（飞书 → “收到”）

当前 `main.py` 已实现最小可用链路：

- 监听所有 p2p / group / thread 消息
- 使用占位 `agent_fn`，无论输入什么，统一回复 **“收到，session={session_id}”**

启动命令（虚拟环境已激活的前提下）：

```bash
python -m xiaopaw.main
```

成功后：

- 当你的飞书应用通过 WebSocket 收到任意消息（单聊、群聊、话题），就会经过 `FeishuListener` → `Runner` → `FeishuSender`，在对应会话中回复一条「收到」。

同时，会自动启动：

- JSON 行格式日志：`data/logs/xiaopaw.log`
- Prometheus 指标端点：`http://127.0.0.1:9100/metrics`

> 注意：确保飞书开放平台 WebSocket 地址、权限范围等已正确配置，否则可能无法收到事件或无法发出消息。

### 使用 TestAPI 本地调试

在完整接入飞书前，可以通过测试 HTTP 接口快速验证核心逻辑（见 `DESIGN.md` 第 4.9 节）：

- `xiaopaw/api/test_server.py` 暴露：
  - `POST /api/test/message`：模拟用户发消息，同步返回 Bot 回复
  - `DELETE /api/test/sessions`：清空所有会话数据

示例（伪代码，实际 main wiring 待后续实现）：

```bash
.venv/bin/python -m xiaopaw.api.test_server  # 或在 main 中集成
```

然后通过 HTTP 客户端（curl / HTTPie / Postman）发送：

```bash
curl -X POST http://127.0.0.1:9090/api/test/message \
  -H "Content-Type: application/json" \
  -d '{
    "routing_key": "p2p:ou_test001",
    "content": "你好"
  }'
```

返回体会包含：

- `reply`: 当前 Runner 使用的 agent_fn 输出
- `session_id`: 使用的对话 ID
- `duration_ms`: 处理耗时

### 运行测试

项目已有较完整的单元测试与集成测试，建议在修改代码后运行：

```bash
.venv/bin/python -m pytest tests/ -v --cov=xiaopaw --cov-report=term-missing
```

或按模块运行：

```bash
.venv/bin/python -m pytest tests/unit/test_runner.py -v
.venv/bin/python -m pytest tests/unit/test_cron_service.py -v
```

### 后续开发路线（简要）

- 将现有 CrewAI 主 Agent（`agents/main_crew.py`）接入 Runner，并实现 `SkillLoaderTool`
- 实现 `agents/skill_crew.py`，打通任务型 Skill 与 AIO-Sandbox 的 Sub-Crew 调用
- 实现 `cleanup/service.py` 并在 `main.py` 中接入 CronService / CleanupService / TestAPI 启动逻辑

当前 README 主要面向「拉仓库 → 配配置 → 跑起来一个最小可用版本」的场景，更多设计细节与进阶用法请参考 `DESIGN.md` 和 `CLAUDE.md`。

