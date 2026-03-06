---
name: feishu_ops
description: "飞书操作：读取云文档内容、向指定用户或群组发送消息、获取群成员列表。适合需要与飞书平台交互的任务，如推送通知、读取共享文档、批量发送报告等。"
type: task
version: "1.0"
---

# feishu_ops Skill

## 功能说明

本 Skill 提供飞书（Lark）平台的核心操作能力，包括：
1. 读取飞书云文档（Doc/Sheet）内容
2. 向指定用户（open_id）或群组（chat_id）发送消息
3. 查询群成员列表

所有飞书 API 调用使用沙盒中的凭证文件，**不得**将 app_id / app_secret 传递给 LLM。

---

## 凭证获取

飞书应用凭证位于沙盒路径 `/workspace/.config/feishu.json`，格式：

```json
{
  "app_id": "cli_xxxx",
  "app_secret": "xxxxxxxx"
}
```

使用 `sandbox_file_operations` 读取后，通过 HTTP 请求飞书 API。

---

## 一、获取飞书 Access Token

所有 API 调用前需先获取 tenant_access_token：

```python
import json
import uuid
import requests

# 读取凭证
with open("/workspace/.config/feishu.json") as f:
    creds = json.load(f)

resp = requests.post(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    json={"app_id": creds["app_id"], "app_secret": creds["app_secret"]},
    timeout=10,
)
data = resp.json()
if data.get("code") != 0:
    raise ValueError(f"获取 tenant_access_token 失败：code={data.get('code')}, msg={data.get('msg')}")
token = data["tenant_access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
```

---

## 二、发送消息

### 2.1 发送文字消息给用户（p2p）

```python
import uuid

requests.post(
    "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
    headers=headers,
    json={
        "receive_id": "ou_xxxx",           # 接收者 open_id
        "msg_type": "text",
        "content": json.dumps({"text": "你好，这是消息内容"}),
        "uuid": str(uuid.uuid4()),          # 防重复发送（每次调用生成新 UUID）
    },
    timeout=10,
)
```

### 2.2 发送消息到群组

```python
requests.post(
    "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
    headers=headers,
    json={
        "receive_id": "oc_xxxx",           # 群组 chat_id
        "msg_type": "text",
        "content": json.dumps({"text": "群组消息内容"}),
    },
    timeout=10,
)
```

### 2.3 发送富文本（post 格式）

```python
content = {
    "zh_cn": {
        "title": "消息标题",
        "content": [
            [{"tag": "text", "text": "第一段内容"}],
            [{"tag": "a", "text": "点击链接", "href": "https://example.com"}],
        ]
    }
}
requests.post(
    "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
    headers=headers,
    json={
        "receive_id": "ou_xxxx",
        "msg_type": "post",
        "content": json.dumps(content),
    },
    timeout=10,
)
```

---

## 三、读取飞书云文档

### 3.1 获取文档纯文本内容

```python
doc_token = "doccnxxxxxxxx"  # 从文档 URL 中提取
resp = requests.get(
    f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/raw_content",
    headers=headers,
    timeout=30,
)
content = resp.json()["data"]["content"]
```

### 3.2 读取电子表格数据

```python
spreadsheet_token = "shtcnxxxxxxxx"
sheet_id = "Sheet1"
resp = requests.get(
    f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{sheet_id}",
    headers=headers,
    timeout=30,
)
values = resp.json()["data"]["valueRange"]["values"]
```

---

## 四、查询群成员

```python
chat_id = "oc_xxxx"
members = []
page_token = ""

while True:
    params = {"member_id_type": "open_id", "page_size": 100}
    if page_token:
        params["page_token"] = page_token
    resp = requests.get(
        f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}/members",
        headers=headers,
        params=params,
        timeout=10,
    )
    data = resp.json()["data"]
    members.extend(data.get("items", []))
    if not data.get("has_more"):
        break
    page_token = data.get("page_token", "")
```

---

## 五、错误处理

飞书 API 返回 `code != 0` 时视为错误：

```python
result = resp.json()
if result.get("code") != 0:
    raise ValueError(f"飞书 API 错误：code={result['code']}, msg={result.get('msg')}")
```

常见错误码：
- `99991663`：token 失效，重新获取后重试
- `230013`：消息内容格式错误，检查 content JSON
- `230002`：接收方 ID 无效，确认 open_id / chat_id 是否正确

---

## 六、输出格式规范

成功时返回：
```json
{
  "errcode": 0,
  "errmsg": "success",
  "data": {
    "message_id": "om_xxxx",       // 发送消息时
    "content": "文档内容...",       // 读取文档时
    "members": [...]               // 查询群成员时
  }
}
```

失败时返回：
```json
{
  "errcode": 1,
  "errmsg": "飞书 API 调用失败：code=230013, msg=...\n建议：检查 receive_id 格式是否正确，p2p 消息用 open_id，群消息用 chat_id",
  "data": {}
}
```
