"""feishu_ops scripts 单元测试

测试策略：
- 通过 sys.path 注入导入 scripts 目录下的模块（不运行子进程）
- 用 unittest.mock 模拟 requests 调用和凭证文件
- 用 capsys 捕获 stdout JSON 输出
- 用 pytest.raises(SystemExit) 捕获 sys.exit(0)

运行：
    python3 -m pytest tests/unit/test_feishu_ops_scripts.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# ── scripts 目录注入 ──────────────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "xiaopaw" / "skills" / "feishu_ops" / "scripts"


def _import_script(name: str):
    """动态导入 feishu_ops scripts 目录下的模块。"""
    if SCRIPTS_DIR not in [Path(p) for p in sys.path]:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import importlib
    return importlib.import_module(name)


# ── 测试工具 ──────────────────────────────────────────────────────────────────

def _make_token_resp(token: str = "t-test-token") -> MagicMock:
    """构造飞书 tenant_access_token 接口的 mock 响应。"""
    m = MagicMock()
    m.json.return_value = {"code": 0, "tenant_access_token": token}
    return m


def _make_api_resp(code: int = 0, data: dict | None = None) -> MagicMock:
    """构造飞书 REST API 的 mock 响应。"""
    m = MagicMock()
    m.json.return_value = {"code": code, "data": data or {}}
    return m


FAKE_CREDS = json.dumps({"app_id": "cli_test", "app_secret": "secret_test"})


# ─────────────────────────────────────────────────────────────────────────────
# _feishu_auth 模块测试
# ─────────────────────────────────────────────────────────────────────────────


class TestFeishuAuth:
    """测试 _feishu_auth.py 的工具函数。"""

    def setup_method(self):
        self.auth = _import_script("_feishu_auth")

    def test_parse_routing_key_p2p(self):
        rtype, rid = self.auth.parse_routing_key("p2p:ou_abc123")
        assert rtype == "open_id"
        assert rid == "ou_abc123"

    def test_parse_routing_key_group(self):
        rtype, rid = self.auth.parse_routing_key("group:oc_xyz456")
        assert rtype == "chat_id"
        assert rid == "oc_xyz456"

    def test_parse_routing_key_bare_ou(self):
        rtype, rid = self.auth.parse_routing_key("ou_direct")
        assert rtype == "open_id"
        assert rid == "ou_direct"

    def test_parse_routing_key_bare_oc(self):
        rtype, rid = self.auth.parse_routing_key("oc_direct")
        assert rtype == "chat_id"
        assert rid == "oc_direct"

    def test_parse_doc_token_from_url(self):
        url = "https://company.feishu.cn/docx/doccnABCDE12345"
        assert self.auth.parse_doc_token(url) == "doccnABCDE12345"

    def test_parse_doc_token_from_legacy_url(self):
        url = "https://company.feishu.cn/docs/doccnABCDE12345"
        assert self.auth.parse_doc_token(url) == "doccnABCDE12345"

    def test_parse_doc_token_bare(self):
        assert self.auth.parse_doc_token("doccnABCDE12345") == "doccnABCDE12345"

    def test_parse_sheet_token_from_url(self):
        url = "https://company.feishu.cn/sheets/shtcnXXXXXX"
        assert self.auth.parse_sheet_token(url) == "shtcnXXXXXX"

    def test_parse_sheet_token_bare(self):
        assert self.auth.parse_sheet_token("shtcnXXXXXX") == "shtcnXXXXXX"

    def test_output_ok_prints_json(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self.auth.output_ok({"message_id": "om_xxx"})
        assert exc.value.code == 0
        out = capsys.readouterr().out
        result = json.loads(out)
        assert result["errcode"] == 0
        assert result["data"]["message_id"] == "om_xxx"

    def test_output_error_prints_json(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self.auth.output_error("API 调用失败", hint="检查 routing_key")
        assert exc.value.code == 0
        out = capsys.readouterr().out
        result = json.loads(out)
        assert result["errcode"] == 1
        assert "API 调用失败" in result["errmsg"]
        assert "检查 routing_key" in result["errmsg"]

    def test_get_headers_calls_token_api(self):
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
            with patch("requests.post", return_value=_make_token_resp("t-abc")) as mock_post:
                headers = self.auth.get_headers()
        mock_post.assert_called_once()
        assert headers["Authorization"] == "Bearer t-abc"
        assert headers["Content-Type"] == "application/json"

    def test_get_headers_exits_on_auth_failure(self, capsys):
        fail_resp = MagicMock()
        fail_resp.json.return_value = {"code": 99991663, "msg": "token expired"}
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
            with patch("requests.post", return_value=fail_resp):
                with pytest.raises(SystemExit):
                    self.auth.get_headers()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "99991663" in out["errmsg"]


# ─────────────────────────────────────────────────────────────────────────────
# send_text.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestSendText:
    def setup_method(self):
        self.mod = _import_script("send_text")

    def _run(self, argv: list[str], api_resp: MagicMock) -> dict:
        """运行 main() 并返回 stdout JSON。"""
        with patch("sys.argv", ["send_text.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]) as _mock:
                    with pytest.raises(SystemExit):
                        self.mod.main()
        import io, contextlib
        return _mock

    def test_sends_to_p2p(self, capsys):
        api_resp = _make_api_resp(data={"message_id": "om_p2p_001"})
        with patch("sys.argv", ["send_text.py", "--routing_key", "p2p:ou_user1", "--text", "Hello"]):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["message_id"] == "om_p2p_001"

    def test_sends_to_group(self, capsys):
        api_resp = _make_api_resp(data={"message_id": "om_grp_001"})
        with patch("sys.argv", ["send_text.py", "--routing_key", "group:oc_chat1", "--text", "群组消息"]):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0

    def test_api_error_returns_errcode_1(self, capsys):
        api_resp = _make_api_resp(code=230002)
        api_resp.json.return_value = {"code": 230002, "msg": "invalid receive_id"}
        with patch("sys.argv", ["send_text.py", "--routing_key", "p2p:ou_bad", "--text", "Hi"]):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "230002" in out["errmsg"]


# ─────────────────────────────────────────────────────────────────────────────
# send_post.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestSendPost:
    def setup_method(self):
        self.mod = _import_script("send_post")

    def test_sends_post_message(self, capsys):
        api_resp = _make_api_resp(data={"message_id": "om_post_001"})
        argv = [
            "--routing_key", "p2p:ou_user1",
            "--title", "测试标题",
            "--paragraphs", '["第一段", "第二段"]',
        ]
        with patch("sys.argv", ["send_post.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["message_id"] == "om_post_001"

    def test_invalid_paragraphs_json(self, capsys):
        argv = [
            "--routing_key", "p2p:ou_user1",
            "--paragraphs", "not-json",
        ]
        with patch("sys.argv", ["send_post.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "JSON" in out["errmsg"]

    def test_parse_paragraph_with_link(self):
        mod = _import_script("send_post")
        elements = mod._parse_paragraph("查看[飞书文档](https://example.com)了解详情")
        tags = [e["tag"] for e in elements]
        assert "a" in tags
        assert "text" in tags
        # 链接元素包含 href
        link_elem = next(e for e in elements if e["tag"] == "a")
        assert link_elem["href"] == "https://example.com"


# ─────────────────────────────────────────────────────────────────────────────
# send_image.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestSendImage:
    def setup_method(self):
        self.mod = _import_script("send_image")

    def test_uploads_and_sends_image(self, capsys, tmp_path):
        # 创建假图片文件
        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        upload_resp = _make_api_resp(data={"image_key": "img_test_001"})
        send_resp = _make_api_resp(data={"message_id": "om_img_001"})

        argv = ["--routing_key", "p2p:ou_user1", "--image_path", str(img_file)]
        with patch("sys.argv", ["send_image.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                # 模拟：1次 token 请求，1次上传，1次发送
                with patch("requests.post", side_effect=[_make_token_resp(), upload_resp, send_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["image_key"] == "img_test_001"
        assert out["data"]["message_id"] == "om_img_001"

    def test_missing_image_file(self, capsys):
        argv = ["--routing_key", "p2p:ou_user1", "--image_path", "/nonexistent/path/img.png"]
        with patch("sys.argv", ["send_image.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "不存在" in out["errmsg"]


# ─────────────────────────────────────────────────────────────────────────────
# send_file.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestSendFile:
    def setup_method(self):
        self.mod = _import_script("send_file")

    def test_file_type_detection(self):
        mod = _import_script("send_file")
        assert mod._feishu_file_type(Path("report.pdf")) == "pdf"
        assert mod._feishu_file_type(Path("data.xlsx")) == "xls"
        assert mod._feishu_file_type(Path("slides.pptx")) == "ppt"
        assert mod._feishu_file_type(Path("video.mp4")) == "mp4"
        assert mod._feishu_file_type(Path("unknown.zip")) == "stream"

    def test_uploads_and_sends_file(self, capsys, tmp_path):
        pdf_file = tmp_path / "report.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake content")

        upload_resp = _make_api_resp(data={"file_key": "file_test_001"})
        send_resp = _make_api_resp(data={"message_id": "om_file_001"})

        argv = ["--routing_key", "p2p:ou_user1", "--file_path", str(pdf_file)]
        with patch("sys.argv", ["send_file.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), upload_resp, send_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["file_key"] == "file_test_001"
        assert out["data"]["file_name"] == "report.pdf"
        assert out["data"]["file_type"] == "pdf"

    def test_missing_file(self, capsys):
        argv = ["--routing_key", "p2p:ou_user1", "--file_path", "/nonexistent/file.pdf"]
        with patch("sys.argv", ["send_file.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "不存在" in out["errmsg"]


# ─────────────────────────────────────────────────────────────────────────────
# read_doc.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestReadDoc:
    def setup_method(self):
        self.mod = _import_script("read_doc")

    def test_reads_doc_by_token(self, capsys):
        api_resp = _make_api_resp(data={"content": "文档正文内容"})
        argv = ["--doc", "doccnABCDE12345"]
        with patch("sys.argv", ["read_doc.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", return_value=api_resp):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["content"] == "文档正文内容"
        assert out["data"]["doc_token"] == "doccnABCDE12345"

    def test_reads_doc_by_url(self, capsys):
        api_resp = _make_api_resp(data={"content": "URL 文档内容"})
        url = "https://company.feishu.cn/docx/doccnURLTOKEN"
        argv = ["--doc", url]
        with patch("sys.argv", ["read_doc.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", return_value=api_resp) as mock_get:
                        with pytest.raises(SystemExit):
                            self.mod.main()
        # 确认 URL 中的 token 被正确提取
        call_url = mock_get.call_args[0][0]
        assert "doccnURLTOKEN" in call_url
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# read_sheet.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestReadSheet:
    def setup_method(self):
        self.mod = _import_script("read_sheet")

    def test_reads_sheet_with_range(self, capsys):
        data_rows = [["月份", "销售额"], ["1月", 10000], ["2月", 15000]]
        api_resp = _make_api_resp(data={"valueRange": {"values": data_rows}})
        argv = ["--sheet", "shtcnXXXXXX", "--sheet_id", "abc123", "--range", "A1:B3"]
        with patch("sys.argv", ["read_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", return_value=api_resp):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["row_count"] == 3
        assert out["data"]["values"][0] == ["月份", "销售额"]

    def test_auto_detects_first_sheet(self, capsys):
        sheets_resp = _make_api_resp(data={"sheets": [{"sheet_id": "auto_sheet"}]})
        data_resp = _make_api_resp(data={"valueRange": {"values": [["A", "B"]]}})
        argv = ["--sheet", "shtcnXXXXXX"]
        with patch("sys.argv", ["read_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", side_effect=[sheets_resp, data_resp]):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["sheet_id"] == "auto_sheet"


# ─────────────────────────────────────────────────────────────────────────────
# get_chat_members.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestGetChatMembers:
    def setup_method(self):
        self.mod = _import_script("get_chat_members")

    def test_returns_member_list(self, capsys):
        members = [{"open_id": "ou_aaa", "name": "张三"}, {"open_id": "ou_bbb", "name": "李四"}]
        api_resp = _make_api_resp(data={"items": members, "has_more": False})
        argv = ["--chat_id", "oc_test_chat"]
        with patch("sys.argv", ["get_chat_members.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", return_value=api_resp):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["member_count"] == 2
        assert out["data"]["members"][0]["open_id"] == "ou_aaa"

    def test_handles_pagination(self, capsys):
        page1 = _make_api_resp(data={
            "items": [{"open_id": "ou_aaa"}],
            "has_more": True,
            "page_token": "pt_001",
        })
        page2 = _make_api_resp(data={
            "items": [{"open_id": "ou_bbb"}],
            "has_more": False,
        })
        argv = ["--chat_id", "oc_test_chat"]
        with patch("sys.argv", ["get_chat_members.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", side_effect=[page1, page2]):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["data"]["member_count"] == 2
