"""创建飞书文档（Docx）。

用法：
    python create_doc.py --title "季度报告" [--folder_token <token>]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import requests  # noqa: E402
from _feishu_auth import check_feishu_resp, get_headers, output_ok  # noqa: E402

_BASE = "https://open.feishu.cn/open-apis"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True, help="文档标题")
    parser.add_argument(
        "--folder_token",
        default="",
        help="存放文档的云空间文件夹 token（留空则放在 我的云空间 根目录）",
    )
    args = parser.parse_args()

    body: dict = {"title": args.title}
    if args.folder_token:
        body["folder_token"] = args.folder_token

    resp = requests.post(
        f"{_BASE}/docx/v1/documents",
        headers=get_headers(),
        json=body,
        timeout=15,
    )
    data = resp.json()
    check_feishu_resp(data, "请确认 folder_token 正确，且应用已有该文件夹的编辑权限")

    doc = data["data"]["document"]
    document_id = doc.get("document_id", "")
    url = f"https://open.feishu.cn/docx/{document_id}"

    output_ok({"document_id": document_id, "url": url, "title": args.title})


if __name__ == "__main__":
    main()
