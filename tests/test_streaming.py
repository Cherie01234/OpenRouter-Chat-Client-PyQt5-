"""
ストリーミング表示とキャンセル。

キャンセルは「スレッドの停止を待つ」設計にしてはいけない。
受信待ちでブロックしていると、close() でも shutdown() でも
リードタイムアウトまで解除できないことが実測で分かっているため、
ワーカーを切り離して UI だけ先に確定させている。
"""
import pytest
from PyQt5.QtCore import QThread

import GUI
from GUI import Message


DEEPSEEK = "deepseek/deepseek-v4-pro"
GROK     = "x-ai/grok-4.3"


def stream(window, model, chunks):
    """ワーカー無しでストリーミング表示だけを再現する。"""
    window._start_streaming_display(model)
    for chunk in chunks:
        window._on_chunk_received(chunk)
    window._flush_stream()


class TestSignalNames:
    def test_qthread_finished_is_not_shadowed(self):
        """
        ApiWorker のシグナルを finished という名前にすると QThread 本来の
        finished を覆い隠し、スレッド終了を検知できなくなる。
        """
        assert GUI.ApiWorker.finished is QThread.finished
        assert hasattr(GUI.ApiWorker, "response_finished")


class TestStreamingDisplay:
    def test_caret_is_shown_while_streaming(self, window):
        stream(window, DEEPSEEK, ["こんに", "ちは"])
        assert window.conversation_text.toPlainText().endswith("▌")

    def test_markdown_is_applied_when_finished(self, window):
        stream(window, DEEPSEEK, ["**太字** です"])
        window._on_stream_finished("", {}, False)
        shown = window.conversation_text.toPlainText()
        assert "太字 です" in shown and "**" not in shown
        assert "▌" not in shown

    def test_raw_markdown_is_kept_in_history(self, window):
        stream(window, DEEPSEEK, ["**太字**"])
        window._on_stream_finished("", {}, False)
        assert window.conversation_history[-1].content[0]["text"] == "**太字**"

    def test_response_records_its_model(self, window):
        stream(window, GROK, ["返答"])
        window._on_stream_finished("", {}, False)
        assert window.conversation_history[-1].model == GROK

    def test_reasoning_and_usage_are_stored(self, window):
        stream(window, DEEPSEEK, ["返答"])
        window._on_stream_finished("推論の中身", {"completion_tokens": 12}, False)
        last = window.conversation_history[-1]
        assert last.reasoning == "推論の中身"
        assert last.usage["completion_tokens"] == 12

    def test_scroll_does_not_jump_when_reading_back(self, window):
        """読み返している最中に最下部へ引き戻さない。"""
        window.resize(600, 300)
        window.conversation_text.setPlainText("x\n" * 500)
        bar = window.conversation_text.verticalScrollBar()
        bar.setValue(0)
        stream(window, DEEPSEEK, ["追記" * 200])
        assert bar.value() == 0


class TestCancel:
    def test_partial_text_and_history_agree(self, window):
        stream(window, DEEPSEEK, ["こんに", "ちは。"])
        window._on_stream_finished("", {}, True)

        shown = window.conversation_text.toPlainText()
        assert "（キャンセルされました）" in shown
        assert "こんにちは。" in shown
        assert "▌" not in shown
        # 画面に残す以上、履歴にも残す
        assert window.conversation_history[-1].text == "こんにちは。"

    def test_buttons_are_restored(self, window):
        stream(window, DEEPSEEK, ["部分"])
        window._on_stream_finished("", {}, True)
        assert window.send_button.isEnabled()
        assert not window.cancel_button.isEnabled()

    def test_zero_chunk_cancel_removes_the_whole_message(self, window):
        window._start_streaming_display(GROK)
        assert "Grok:" in window.conversation_text.toPlainText()
        window._on_stream_finished("", {}, True)
        assert "Grok:" not in window.conversation_text.toPlainText()
        assert window.conversation_history == []

    def test_cancel_keeps_previous_reasoning(self, window):
        window.reasoning_text.setPlainText("前回の推論")
        stream(window, DEEPSEEK, ["部分"])
        window._on_stream_finished("", {}, True)
        assert window.reasoning_text.toPlainText() == "前回の推論"

    def test_cancel_without_request_is_a_noop(self, window):
        window._cancel_request()
        assert window.conversation_history == []


class TestBusyGuard:
    """
    送信ボタンを無効化しても Ctrl+Enter は素通りする。
    二重送信するとワーカーとストリーム位置が上書きされ、表示が混線する。
    """

    class _RunningWorker:
        def isRunning(self):
            return True

    def test_ctrl_enter_is_blocked_while_streaming(self, window):
        window.worker = self._RunningWorker()
        window.message_input.setPlainText("二重送信")
        window.message_input.send_requested.emit()

        assert window.conversation_history == []
        # 入力内容を失わせない
        assert window.message_input.toPlainText() == "二重送信"
        assert "応答中" in window.statusBar().currentMessage()


class TestErrorPath:
    def test_partial_stream_is_removed_so_retry_is_safe(self, window, auto_dialog):
        """
        エラー時に部分応答を消さないと、再試行で本文が二重になる。
        """
        auto_dialog(0)                                  # 再試行しない
        stream(window, DEEPSEEK, ["途中まで"])
        window._handle_api_error("APIエラー: 502")

        shown = window.conversation_text.toPlainText()
        assert "途中まで" not in shown
        assert "エラー" in shown
        # 履歴にも入れない
        assert all(m.role != "assistant" for m in window.conversation_history)
