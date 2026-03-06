---
name: scheduler_mgr
description: "定时任务管理：创建、查看、删除定时任务。支持一次性（at）、周期（every）、cron 表达式三种触发模式。写入 data/cron/tasks.json，CronService 自动热重载生效，无需重启进程。"
type: task
version: "1.0"
---

# scheduler_mgr Skill

## 功能说明

本 Skill 管理 XiaoPaw 的定时任务，通过读写 `tasks.json` 文件实现：
- **创建**定时任务（三种触发模式）
- **查看**当前所有任务
- **删除**指定任务

修改后 CronService 会在下次 tick 时自动感知文件变化（mtime 热重载），无需重启进程。

---

## 数据文件路径

定时任务配置文件通过独立 Docker 卷挂载至沙盒，可直接读写：

```
沙盒路径：/workspace/cron/tasks.json
宿主机路径：data/cron/tasks.json（挂载配置：./data/cron:/workspace/cron:rw）
```

所有读写操作均使用沙盒路径 `/workspace/cron/tasks.json`。

---

## 一、数据结构

```json
{
  "version": 1,
  "jobs": [
    {
      "id": "job-uuid",
      "name": "每日工作摘要",
      "enabled": true,
      "schedule": {
        "kind": "cron",
        "expr": "0 9 * * 1-5",
        "tz": "Asia/Shanghai",
        "at_ms": null,
        "every_ms": null
      },
      "payload": {
        "routing_key": "p2p:ou_xxxx",
        "message": "请生成今日工作摘要"
      },
      "state": {
        "next_run_at_ms": 1738800000000,
        "last_run_at_ms": null,
        "last_status": null,
        "last_error": null
      },
      "created_at_ms": 1736900000000,
      "updated_at_ms": 1736900000000,
      "delete_after_run": false
    }
  ]
}
```

---

## 二、三种触发模式

### 2.1 一次性任务（at）

```json
"schedule": {
  "kind": "at",
  "at_ms": 1738800000000,   // 触发时刻的 Unix 毫秒时间戳
  "every_ms": null,
  "expr": null,
  "tz": null
}
```

- `delete_after_run: true`：触发后自动删除
- `delete_after_run: false`：触发后设为 `enabled: false`（保留记录）

### 2.2 周期任务（every）

```json
"schedule": {
  "kind": "every",
  "every_ms": 3600000,      // 间隔毫秒数（此处为 1 小时）
  "at_ms": null,
  "expr": null,
  "tz": null
}
```

常用换算：
- 1 分钟 = 60000 ms
- 1 小时 = 3600000 ms
- 1 天 = 86400000 ms

### 2.3 Cron 表达式（cron）

```json
"schedule": {
  "kind": "cron",
  "expr": "0 9 * * 1",      // 每周一 09:00
  "tz": "Asia/Shanghai",
  "at_ms": null,
  "every_ms": null
}
```

常用 cron 表达式：
- `0 9 * * 1-5`：周一至周五 09:00
- `0 18 * * 5`：每周五 18:00
- `0 */2 * * *`：每 2 小时整点
- `30 8 1 * *`：每月 1 日 08:30

---

## 三、操作步骤

### 3.1 读取当前任务列表

```python
import json

# 读取任务文件（沙盒路径）
result = sandbox_file_operations(action="read", path="/workspace/cron/tasks.json")
if result.get("error"):
    # 文件不存在，初始化空结构
    store = {"version": 1, "jobs": []}
else:
    store = json.loads(result["content"])
```

### 3.2 创建新任务

```python
import uuid
import time

new_job = {
    "id": f"job-{uuid.uuid4().hex[:8]}",
    "name": "任务名称",
    "enabled": True,
    "schedule": {
        "kind": "cron",          # at / every / cron
        "expr": "0 9 * * 1",
        "tz": "Asia/Shanghai",
        "at_ms": None,
        "every_ms": None,
    },
    "payload": {
        "routing_key": "p2p:ou_xxxx",     # 触发时发送消息的目标
        "message": "请执行...",
    },
    "state": {
        "next_run_at_ms": None,           # CronService 会自动计算并更新
        "last_run_at_ms": None,
        "last_status": None,
        "last_error": None,
    },
    "created_at_ms": int(time.time() * 1000),
    "updated_at_ms": int(time.time() * 1000),
    "delete_after_run": False,
}

store["jobs"].append(new_job)
```

### 3.3 删除任务

```python
job_id_to_delete = "job-xxxxxxxx"
store["jobs"] = [j for j in store["jobs"] if j["id"] != job_id_to_delete]
```

### 3.4 原子写入文件

```python
# 使用 sandbox_str_replace_editor 创建/覆盖文件
new_content = json.dumps(store, ensure_ascii=False, indent=2)
sandbox_str_replace_editor(
    command="create",
    path="/workspace/cron/tasks.json",
    file_text=new_content,
)
```

---

## 四、payload.routing_key 格式

| 场景 | routing_key 格式 | 示例 |
|------|----------------|------|
| 发给特定用户 | `p2p:{open_id}` | `p2p:ou_abc123` |
| 发到群组 | `group:{chat_id}` | `group:oc_chat456` |
| 发到话题 | `thread:{chat_id}:{thread_id}` | `thread:oc_chat789:ot_xxx` |

routing_key 可从用户当前对话的 session_id 相关信息中获取。

---

## 五、输出格式规范

成功创建：
```json
{
  "errcode": 0,
  "errmsg": "success",
  "data": {
    "action": "created",
    "job_id": "job-xxxxxxxx",
    "name": "每日工作摘要",
    "next_hint": "CronService 将在下次 tick 自动感知并加载新任务，无需重启"
  }
}
```

成功删除：
```json
{
  "errcode": 0,
  "errmsg": "success",
  "data": {
    "action": "deleted",
    "job_id": "job-xxxxxxxx"
  }
}
```

查看列表：
```json
{
  "errcode": 0,
  "errmsg": "success",
  "data": {
    "total": 2,
    "jobs": [
      {"id": "job-xxx", "name": "...", "enabled": true, "schedule": {...}}
    ]
  }
}
```

失败：
```json
{
  "errcode": 1,
  "errmsg": "写入 tasks.json 失败：路径不存在\n建议：确认沙盒挂载路径，或使用 sandbox_execute_bash 创建目录：mkdir -p /workspace/cron",
  "data": {}
}
```
