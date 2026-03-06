> 本文档是 [DESIGN.md](../DESIGN.md) §6 的详细内容
> 最后更新：2026-03-06

## 6. 接口设计

### 6.1 飞书消息接收接口

**协议**：飞书 WebSocket 长连接（lark-oapi `ws.Client`）

**事件类型**：`P2ImMessageReceiveV1`（接收到消息）

**触发条件**：
- 单聊：任意消息
- 群聊：@Bot 消息 或 Bot 是群主
- 话题群：话题内的消息

**无需公网 IP**：WebSocket 由飞书主动推送，适合本地/内网部署。

---

### 6.2 飞书消息发送接口

> 来源：SDK `create_message_request_body.py` + `reply_message_request_body.py`（已验证）

**单聊/群聊 — 新建消息**：

```
POST /open-apis/im/v1/messages?receive_id_type={open_id|chat_id}

请求字段：
  receive_id: str   # open_id（单聊）或 chat_id（群聊）
  msg_type:   str   # "text"
  content:    str   # JSON 字符串：'{"text":"回复内容"}'
  uuid:       str   # 幂等 key，防重发（传入 feishu_msg_id）
```

**话题群 — 在话题内回复**：

```
POST /open-apis/im/v1/messages/:root_id/reply

请求字段：
  content:         str   # 同上
  msg_type:        str
  reply_in_thread: bool  # True = 在话题内回复
  uuid:            str   # 幂等 key
```

**路由规则**：

| routing_key 类型 | 使用的 API |
|-----------------|-----------|
| `p2p:{open_id}` | CreateMessage，receive_id_type=open_id |
| `group:{chat_id}` | CreateMessage，receive_id_type=chat_id |
| `thread:{chat_id}:{thread_id}` | ReplyMessage，message_id=root_id，reply_in_thread=True |

**重试策略**：最多 3 次，指数退避 1s/2s/4s。

---

### 6.3 飞书文件/图片下载接口

> 来源：SDK `get_message_resource_request.py` + `get_message_resource_response.py`（已验证）

**API**：`GET /open-apis/im/v1/messages/:message_id/resources/:file_key?type=image|file`

**参数说明**：

| 参数 | 来源 | 说明 |
|------|------|------|
| `message_id` | EventMessage.message_id | 消息 ID |
| `file_key` | content JSON 的 image_key 或 file_key | 飞书资源 key |
| `type` | message_type | "image" 或 "file" |

**下载逻辑**：
1. 根据 msg_type 解析 content JSON 提取 file_key
2. 图片：resource_type="image"，文件名用 `{image_key}.jpg`（飞书图片无文件名）
3. 文件：resource_type="file"，文件名取 content["file_name"]
4. 调用 `client.im.v1.message_resource.aget(request)` 异步下载
5. 响应 `response.file`（BytesIO）写入 `dest_dir/{actual_name}`
6. 返回本地文件路径（失败返回 None）

**下载后的 user_message 模板**：

```
用户发来了文件，已自动保存至沙盒路径：
`{file_path}`
请根据文件内容和用户意图完成相应处理。
（如有用户备注则附加：用户备注：{original_text}）
```

**调用时机**：Runner 在确定 session_id 后调用 `FeishuDownloader.download()`，将文件写入 `data/workspace/sessions/{sid}/uploads/`，沙盒内可见路径为 `/workspace/sessions/{sid}/uploads/{filename}`。
