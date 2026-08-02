"""
ローカルの疑似 OpenRouter サーバを立てて、通信〜表示までを通しで確認する。

外部へは接続しない。127.0.0.1 に立てた HTTP サーバへ ApiWorker を向ける。
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import GUI
from conftest import wait_until


MODEL = "deepseek/deepseek-v4-pro"


class _StreamState:
    """テスト毎にサーバの振る舞いを差し替えるための入れ物。"""
    chunks: list = []
    usage: dict | None = None
    go_silent = False        # 最後に沈黙して受信待ちでブロックさせる
    status = 200
    release = threading.Event()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send_chunk(self, payload: bytes):
        self.wfile.write(b"%X\r\n" % len(payload) + payload + b"\r\n")
        self.wfile.flush()

    def _sse(self, obj):
        self._send_chunk(f"data: {json.dumps(obj)}\n\n".encode())

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))

        if _StreamState.status != 200:
            body = b'{"error": "upstream failure"}'
            self.send_response(_StreamState.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for chunk in _StreamState.chunks:
                self._sse({"choices": [{"delta": {"content": chunk}}]})
                time.sleep(0.02)
            if _StreamState.go_silent:
                # 解放されるまで沈黙し、受信待ちでブロックさせる
                _StreamState.release.wait(30)
                # 解放後は応答をきちんと終わらせる。
                # 途中で return するだけだと keep-alive で接続が残り、
                # クライアントはリードタイムアウトまで抜けられない
                self._send_chunk(b"data: [DONE]\n\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
                self.close_connection = True
                return
            if _StreamState.usage:
                self._sse({"choices": [{"delta": {}}], "usage": _StreamState.usage})
            self._send_chunk(b"data: [DONE]\n\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass                      # クライアントが切った場合


@pytest.fixture(scope="module")
def fake_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
    _StreamState.release.set()
    server.shutdown()


@pytest.fixture(autouse=True)
def reset_stream(fake_api, monkeypatch):
    _StreamState.chunks = []
    _StreamState.usage = None
    _StreamState.go_silent = False
    _StreamState.status = 200
    _StreamState.release = threading.Event()
    monkeypatch.setattr(GUI.ApiWorker, "URL", fake_api)
    yield
    _StreamState.release.set()


def send(window, text="テスト"):
    window.api_key = "test-key"
    window.message_input.setPlainText(text)
    window.message_input.send_requested.emit()


class TestNormalResponse:
    def test_full_round_trip(self, qapp, window):
        _StreamState.chunks = ["# 見出し\n", "本文 **強調**\n"]
        _StreamState.usage = {"prompt_tokens": 11, "completion_tokens": 22,
                              "cost": 0.0031,
                              "completion_tokens_details": {"reasoning_tokens": 5}}
        window.model_combo.setCurrentText(MODEL)
        send(window)

        assert wait_until(qapp, lambda: not window._is_busy(), timeout=10)

        shown = window.conversation_text.toPlainText()
        assert "見出し" in shown
        assert "**" not in shown                      # Markdown 変換済み
        assert "▌" not in shown

        reply = window.conversation_history[-1]
        assert reply.role == "assistant"
        assert reply.content[0]["text"] == "".join(_StreamState.chunks)
        assert reply.model == MODEL
        assert reply.usage["completion_tokens"] == 22

        status = window.statusBar().currentMessage()
        assert "11" in status and "22" in status
        assert "$" in status                          # コスト表示
        assert window.send_button.isEnabled()

    def test_remote_image_in_the_reply_is_stripped(self, qapp, window):
        _StreamState.chunks = ['<img src="https://evil.example/p.png?leak=1">']
        window.model_combo.setCurrentText(MODEL)
        send(window)

        assert wait_until(qapp, lambda: not window._is_busy(), timeout=10)
        assert "evil.example" not in window.conversation_text.toHtml()


class TestCancel:
    def test_cancel_returns_immediately_even_when_the_socket_is_stuck(self, qapp, window):
        """
        応答が沈黙した状態でも、UI はスレッドの終了を待たずに確定する。
        close()/shutdown() では進行中の recv を解除できないため。
        """
        _StreamState.chunks = ["部分的な", "応答**です**"]
        _StreamState.go_silent = True
        window.model_combo.setCurrentText(MODEL)
        send(window)

        assert wait_until(
            qapp, lambda: window._stream_buffer == "部分的な応答**です**", timeout=10)

        started = time.monotonic()
        window._cancel_request()
        elapsed = time.monotonic() - started

        assert elapsed < 0.5, "キャンセルがソケットの終了を待ってしまっている"

        shown = window.conversation_text.toPlainText()
        assert "（キャンセルされました）" in shown
        assert "応答です" in shown and "**" not in shown
        assert "▌" not in shown
        assert "エラー" not in shown                  # 中断はエラー扱いにしない

        # 画面と履歴が食い違わない
        assert window.conversation_history[-1].content[0]["text"] == "部分的な応答**です**"
        assert window.send_button.isEnabled()
        assert not window._is_busy()

        # 取り残したスレッドは参照を保持しておく（GC されると落ちる）
        assert len(window._retired_workers) == 1
        assert window._retired_workers[0].isRunning()

        _StreamState.release.set()
        assert wait_until(qapp, lambda: not window._retired_workers, timeout=15), \
            "終了したワーカーが片付いていない"


class TestErrorResponse:
    def test_http_error_is_reported_and_history_stays_clean(self, qapp, window,
                                                            auto_dialog):
        auto_dialog(0)                                 # 再試行しない
        _StreamState.status = 503
        window.model_combo.setCurrentText(MODEL)
        send(window)

        assert wait_until(qapp, lambda: not window._is_busy(), timeout=10)

        shown = window.conversation_text.toPlainText()
        assert "エラー" in shown and "503" in shown
        # 応答は履歴に入れない（再送しても本文が二重にならない）
        assert all(m.role != "assistant" for m in window.conversation_history)
        assert window.send_button.isEnabled()
