---
name: feishu_ops
description: "飞书操作：向用户/群组发送消息（文字/富文本/图片/文件）、读取云文档/表格内容、查询群成员、管理日历事件。适合推送通知、发送处理结果文件、读取共享文档、批量发送报告等场景。"
type: task
version: "2.0"
---

# feishu_ops Skill

所有操作通过沙盒内的 `scripts/` 目录下的独立脚本执行。脚本自动从 `/workspace/.config/feishu.json` 读取凭证，无需手动处理鉴权。

**调用方式**：`python {_skill_base}/scripts/<脚本名>.py [参数]`

---

## 一、发送消息

### send_text.py — 发送纯文字消息

```
python {_skill_base}/scripts/send_text.py \
    --routing_key <routing_key> \
    --text "消息内容"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--routing_key` | ✅ | `p2p:ou_xxx`（私聊）或 `group:oc_xxx`（群组） |
| `--text` | ✅ | 纯文本消息内容 |

---

### send_post.py — 发送富文本消息（带标题 + 多段落）

```
python {_skill_base}/scripts/send_post.py \
    --routing_key <routing_key> \
    --title "消息标题" \
    --paragraphs '["第一段内容", "第二段，含[链接](https://example.com)"]'
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--routing_key` | ✅ | 同上 |
| `--title` | 否 | 消息标题，可为空 |
| `--paragraphs` | ✅ | JSON 字符串数组，每项为一段文字；支持 `[文字](URL)` 格式内嵌链接 |

---

### send_image.py — 发送图片

```
python {_skill_base}/scripts/send_image.py \
    --routing_key <routing_key> \
    --image_path /workspace/sessions/{session_id}/outputs/chart.png
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--routing_key` | ✅ | 同上 |
| `--image_path` | ✅ | 沙盒内图片绝对路径（jpg/png/gif/webp，≤30MB） |

脚本自动完成：上传图片 → 获取 image_key → 发送 image 消息。

---

### send_file.py — 发送文件（处理结果回传核心场景）

```
python {_skill_base}/scripts/send_file.py \
    --routing_key <routing_key> \
    --file_path /workspace/sessions/{session_id}/outputs/report.pdf
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--routing_key` | ✅ | 同上 |
| `--file_path` | ✅ | 沙盒内文件绝对路径（pdf/doc/xls/ppt/mp4/opus 等，≤30MB） |

脚本自动完成：上传文件 → 获取 file_key → 发送 file 消息。

**典型用法**：用户上传文件 → pdf/docx/xlsx 等 Skill 处理 → 结果保存到 `outputs/` → 调用本脚本将结果文件发回给用户。

---

## 二、读取飞书云文档

### read_doc.py — 读取飞书文档纯文本

```
python {_skill_base}/scripts/read_doc.py \
    --doc "https://xxx.feishu.cn/docx/doccnXXXXXX"
# 或直接传 token：
python {_skill_base}/scripts/read_doc.py --doc doccnXXXXXX
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--doc` | ✅ | 飞书文档 URL 或 doc_token，脚本自动解析 |

返回 `data.content`（纯文本字符串）。

---

### read_sheet.py — 读取飞书电子表格数据

```
python {_skill_base}/scripts/read_sheet.py \
    --sheet "https://xxx.feishu.cn/sheets/shtcnXXXXXX" \
    --sheet_id Sheet1 \
    --range A1:D10
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--sheet` | ✅ | 电子表格 URL 或 spreadsheet_token |
| `--sheet_id` | 否 | Sheet 的 sheetId（非 Sheet 名称），不填则读第一个 Sheet |
| `--range` | 否 | 读取范围如 `A1:D10`，不填则读整表 |

返回 `data.values`（二维数组）。

---

## 三、查询群成员

### get_chat_members.py — 获取群组成员列表

```
python {_skill_base}/scripts/get_chat_members.py --chat_id oc_xxxxx
```

返回 `data.members`（含 open_id、name 等字段的数组）。

---

## 四、日历操作

> 仅支持应用已订阅的共享日历，不支持用户个人 primary 日历（需 user_access_token）。

### list_events.py — 查询日历事件

```
python {_skill_base}/scripts/list_events.py \
    --calendar_id feishu_xxxxxx \
    --start_time 2026-03-01T00:00:00+08:00 \
    --end_time 2026-03-31T23:59:59+08:00
```

### create_event.py — 创建日历事件

```
python {_skill_base}/scripts/create_event.py \
    --calendar_id feishu_xxxxxx \
    --summary "周例会" \
    --start_time 2026-03-09T10:00:00+08:00 \
    --end_time 2026-03-09T11:00:00+08:00 \
    --description "本周进度同步" \
    --attendees '["ou_aaa", "ou_bbb"]'
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--calendar_id` | ✅ | 日历 ID |
| `--summary` | ✅ | 事件标题 |
| `--start_time` / `--end_time` | ✅ | RFC3339 格式时间 |
| `--description` | 否 | 事件描述 |
| `--attendees` | 否 | JSON 数组，元素为与会者 open_id |

---

## 输出格式

所有脚本统一输出 JSON 到 stdout，exit 0：

```json
{"errcode": 0, "errmsg": "success", "data": {...}}
{"errcode": 1, "errmsg": "错误说明\n建议：...", "data": {}}
```

`errcode=0` 表示成功，`errcode=1` 表示失败（`errmsg` 包含具体原因和建议）。
