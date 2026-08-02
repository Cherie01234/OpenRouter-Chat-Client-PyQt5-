"""
実際に描画させて、外部への取得が1件も起きないことを確かめる。

loadResource() を直接呼ぶだけでは遅延ロード経路を通らない。
localhost に受付を記録するサーバを立て、そこを指す画像を含む応答を
表示・描画させたうえで、受付件数が 0 であることを確認する。
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PyQt5.QtGui import QImage, QColor, QPainter

import GUI
from GUI import Message


class _TrapHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).hits.append(self.path)
        body = b"\x89PNG\r\n\x1a\n"
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_HEAD = do_GET


@pytest.fixture
def trap_server():
    _TrapHandler.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TrapHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", _TrapHandler.hits
    server.shutdown()


def force_render(widget, qapp):
    """遅延ロードを起こさせるため、実際に描画まで行う。"""
    widget.resize(400, 300)
    widget.show()
    qapp.processEvents()
    document = widget.document()
    document.adjustSize()
    canvas = QImage(400, 300, QImage.Format_RGB32)
    canvas.fill(QColor("black"))
    painter = QPainter(canvas)
    document.drawContents(painter)
    painter.end()
    qapp.processEvents()


class TestNoOutboundFetchOnRender:
    def test_model_reply_with_remote_image(self, qapp, window, trap_server):
        base, hits = trap_server
        window.conversation_history = [Message.assistant(
            f'<img src="{base}/from-model.png">',
            model="x-ai/grok-4.3")]
        window._redraw_conversation()
        force_render(window.conversation_text, qapp)

        assert hits == [], f"モデル応答から外部取得が発生した: {hits}"

    def test_markdown_image_syntax(self, qapp, window, trap_server):
        base, hits = trap_server
        window.conversation_history = [Message.assistant(
            f"![説明]({base}/markdown.png)", model="x-ai/grok-4.3")]
        window._redraw_conversation()
        force_render(window.conversation_text, qapp)

        assert hits == [], f"Markdown 記法から外部取得が発生した: {hits}"

    def test_crafted_conversation_file(self, qapp, window, trap_server):
        """サニタイザを通らない、会話ファイル由来の経路。"""
        base, hits = trap_server
        window.conversation_history = [Message("user", [
            {"type": "image_url",
             "image_url": {"url": f'data:image/png;base64,AA" src="{base}/crafted.png'}}
        ])]
        window._redraw_conversation()
        force_render(window.conversation_text, qapp)

        assert hits == [], f"会話ファイル由来で外部取得が発生した: {hits}"

    def test_html_inserted_directly_is_still_blocked(self, qapp, window, trap_server):
        """
        将来サニタイザを通らない挿入経路が増えても止まること
        （loadResource で止めている意味を確かめる）。
        """
        base, hits = trap_server
        window.conversation_text.setHtml(f'<img src="{base}/direct.png" width="50">')
        force_render(window.conversation_text, qapp)

        assert hits == [], f"直接挿入した HTML から外部取得が発生した: {hits}"

    def test_trap_server_actually_records(self, trap_server):
        """罠サーバ自体が働いていることの対照。"""
        import urllib.request
        base, hits = trap_server
        urllib.request.urlopen(f"{base}/control", timeout=5).read()
        assert hits == ["/control"]
