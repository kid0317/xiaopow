"""创建飞书多维表格（Bitable）应用。

用法：
    python create_bitable.py --name "项目管理" [--folder_token <token>]
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
    parser.add_argument("--name", required=True, help="多维表格名称")
    parser.add_argument(
        "--folder_token",
        default="",
        help="存放多维表格的云空间文件夹 token（留空则放在 我的云空间 根目录）",
    )
    args = parser.parse_args()

    body: dict = {"name": args.name}
    if args.folder_token:
        body["folder_token"] = args.folder_token

    resp = requests.post(
        f"{_BASE}/bitable/v1/apps",
        headers=get_headers(),
        json=body,
        timeout=15,
    )
    data = resp.json()
    check_feishu_resp(data, "请确认 folder_token 正确，且应用已有该文件夹的编辑权限")

    app = data["data"]["app"]
    app_token = app.get("app_token", "")
    url = app.get("url", f"https://open.feishu.cn/base/{app_token}")

    output_ok({"app_token": app_token, "url": url, "name": args.name})


if __name__ == "__main__":
    main()
