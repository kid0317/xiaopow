---
name: history_reader
description: 读取当前 Session 的完整历史对话，支持分页，用于在历史被截断时查阅早期记录
type: task
version: "1.0"
---

# history_reader — 历史对话读取

## 功能概述

读取当前 Session 的历史对话记录，支持分页，返回结构化的消息列表。
当主 Agent 上下文中的历史对话被截断时（通常保留最近 20 条），
可通过此 Skill 查询更早期的内容。

## 使用场景

- 用户询问"你之前说的 xxx 是什么意思？"但那条消息已超出上下文窗口
- 需要汇总/回顾历史对话内容
- 统计历史对话中的关键决策或行动

## 输入参数（task_context 中必须包含）

```json
{
  "session_id": "s-xxxx",
  "page": 1,
  "page_size": 20
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | 当前 Session ID，来自 task.description 中的 Session ID |
| `page` | int | 否 | 页码，从 1 开始，默认 1（最后一页 = 最新消息） |
| `page_size` | int | 否 | 每页条数，1-50，默认 20 |

**分页规则**：page=1 返回最旧的 page_size 条，page 越大越新。
如需最新消息，使用较大 page 或直接从主 Agent 上下文获取。

## 输出格式（SkillResult）

```json
{
  "errcode": 0,
  "message": "成功读取第 1 页，共 35 条消息，本页 20 条",
  "data": {
    "messages": [
      {"role": "user", "content": "...", "ts": 1700000000000},
      {"role": "assistant", "content": "...", "ts": 1700000001000}
    ],
    "total": 35,
    "page": 1,
    "page_size": 20,
    "total_pages": 2
  }
}
```

## 实现说明

历史对话存储在沙盒路径：`/workspace/sessions/{session_id}.jsonl`

> **注意**：JSONL 文件第一行是 meta 行（以 `#` 开头），从第二行起才是消息记录。
> 每行 JSON 格式：`{"role": "user"|"assistant", "content": "...", "ts": 毫秒时间戳}`

### 读取步骤

1. 使用 `sandbox_file_operations` 读取 `/workspace/sessions/{session_id}.jsonl`
2. 跳过第一行（meta 行）
3. 解析每行 JSON，过滤出 `role` 为 `user` 或 `assistant` 的记录
4. 按 page 和 page_size 分页
5. 返回 SkillResult JSON

### 错误处理

- 文件不存在 → errcode=404, message="Session 不存在或无历史记录"
- JSON 解析失败 → 跳过该行，记录 warning 后继续
- page 超出范围 → 返回空 messages，total_pages 正确

## 示例调用

主 Agent 在 task_context 中传入：
```json
{
  "session_id": "s-abc123",
  "page": 1,
  "page_size": 20
}
```

Sub-Agent 执行后返回第 1 页（最旧的 20 条），
主 Agent 可根据 `total_pages` 决定是否继续翻页。
