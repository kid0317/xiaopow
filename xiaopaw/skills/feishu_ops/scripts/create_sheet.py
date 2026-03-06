"""创建飞书电子表格（Spreadsheet）。

用法：
    python create_sheet.py --title "销售数据" [--folder_token <token>]
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
    parser.add_argument("--title", required=True, help="电子表格标题")
    parser.add_argument(
        "--folder_token",
        default="",
        help="存放表格的云空间文件夹 token（留空则放在 我的云空间 根目录）",
    )
    args = parser.parse_args()

    body: dict = {"title": args.title}
    if args.folder_token:
        body["folder_token"] = args.folder_token

    resp = requests.post(
        f"{_BASE}/sheets/v3/spreadsheets",
        headers=get_headers(),
        json=body,
        timeout=15,
    )
    data = resp.json()
    check_feishu_resp(data, "请确认 folder_token 正确，且应用已有该文件夹的编辑权限")

    sheet_info = data["data"]["spreadsheet"]
    token = sheet_info.get("spreadsheet_token", "")
    url = sheet_info.get("url", f"https://open.feishu.cn/sheets/{token}")

    output_ok({"spreadsheet_token": token, "url": url, "title": args.title})


if __name__ == "__main__":
    main()
