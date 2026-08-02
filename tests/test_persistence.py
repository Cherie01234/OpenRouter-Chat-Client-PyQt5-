"""
保存・読み込み・Ctrl+S・終了時の挙動。
"""
import json

import pytest
from PyQt5.QtWidgets import QFileDialog, QMessageBox
from PyQt5.QtGui import QCloseEvent

import GUI
from GUI import Message
from conftest import CATALOG_FIXTURE


def choose_save(monkeypatch, path, counter=None):
    def _dialog(*args, **kwargs):
        if counter is not None:
            counter["calls"] += 1
        return (str(path), "")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(_dialog))


def choose_open(monkeypatch, path):
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(path), "")))


@pytest.fixture
def sample_history():
    return [
        Message.user("第一章を書いて"),
        Message.assistant("彼は歩き出した。", model="x-ai/grok-4.3",
                          reasoning="構成を考えた", usage={"completion_tokens": 42}),
    ]


class TestSaveAndLoad:
    def test_round_trip_keeps_metadata(self, make_window, monkeypatch,
                                       tmp_path, sample_history):
        path = tmp_path / "novel.json"
        saver = make_window()
        saver.conversation_history = list(sample_history)
        choose_save(monkeypatch, path)
        assert saver._save_conversation() is True

        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["version"] == GUI.CONVERSATION_FORMAT_VERSION

        loader = make_window()
        choose_open(monkeypatch, path)
        loader._load_conversation()

        restored = loader.conversation_history[1]
        assert restored.model == "x-ai/grok-4.3"
        assert restored.reasoning == "構成を考えた"
        assert restored.usage["completion_tokens"] == 42

    def test_reasoning_is_restored_to_the_panel(self, make_window, monkeypatch,
                                                tmp_path, sample_history):
        path = tmp_path / "novel.json"
        saver = make_window()
        saver.conversation_history = list(sample_history)
        choose_save(monkeypatch, path)
        saver._save_conversation()

        loader = make_window()
        choose_open(monkeypatch, path)
        loader._load_conversation()
        assert "構成を考えた" in loader.reasoning_text.toPlainText()

    def test_legacy_file_gets_its_model_backfilled(self, window, monkeypatch, tmp_path):
        """
        旧形式には発言ごとのモデルが無い。ファイル全体のモデルで補わないと
        再描画のたびに名前がぶれる。
        """
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps({
            "model": "google/gemini-3-flash-preview",
            "conversation": [
                {"role": "user", "content": "旧形式の質問"},        # content が str
                {"role": "assistant", "content": [{"type": "text", "text": "旧形式の回答"}]},
            ],
        }, ensure_ascii=False), encoding="utf-8")

        choose_open(monkeypatch, path)
        window._load_conversation()

        assert window.conversation_history[0].content == \
            [{"type": "text", "text": "旧形式の質問"}]
        assert window.conversation_history[1].model == "google/gemini-3-flash-preview"
        assert "Gemini: 旧形式の回答" in window.conversation_text.toPlainText()

    def test_cancelled_dialog_reports_failure(self, window, monkeypatch):
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: ("", "")))
        window.conversation_history = [Message.user("Q")]
        assert window._save_conversation() is False


class TestMarkdownExport:
    def test_uses_per_message_model_names(self, window, monkeypatch, tmp_path):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        window.conversation_history = [
            Message.user("質問1"),
            Message.assistant("回答1", model="deepseek/deepseek-v4-pro"),
            Message.user("質問2"),
            Message.assistant("回答2", model="x-ai/grok-4.3"),
        ]
        path = tmp_path / "log.md"
        choose_save(monkeypatch, path)
        window._export_markdown()

        text = path.read_text(encoding="utf-8")
        assert "## DeepSeek\n" in text
        assert "## Grok\n" in text
        assert "deepseek/deepseek-v4-pro" in text and "x-ai/grok-4.3" in text


class TestQuickSave:
    def test_first_save_asks_then_remembers(self, window, monkeypatch,
                                            tmp_path, sample_history):
        counter = {"calls": 0}
        path = tmp_path / "ch1.json"
        choose_save(monkeypatch, path, counter)

        window.conversation_history = list(sample_history)
        window._mark_dirty()
        assert "*" in window.windowTitle()

        assert window._quick_save() is True
        assert counter["calls"] == 1
        assert window._current_path == str(path)
        assert "ch1.json" in window.windowTitle()
        assert "*" not in window.windowTitle()

    def test_second_save_overwrites_silently(self, window, monkeypatch,
                                             tmp_path, sample_history):
        counter = {"calls": 0}
        path = tmp_path / "ch1.json"
        choose_save(monkeypatch, path, counter)

        window.conversation_history = list(sample_history)
        window._quick_save()

        window.conversation_history.append(Message.user("続きを"))
        window._mark_dirty()
        assert window._quick_save() is True
        assert counter["calls"] == 1                      # 2回目は聞かない

        saved = json.loads(path.read_text(encoding="utf-8"))
        assert len(saved["conversation"]) == 3

    def test_loading_sets_the_overwrite_target(self, make_window, monkeypatch,
                                               tmp_path, sample_history):
        path = tmp_path / "ch1.json"
        saver = make_window()
        saver.conversation_history = list(sample_history)
        choose_save(monkeypatch, path)
        saver._quick_save()

        loader = make_window()
        choose_open(monkeypatch, path)
        loader._load_conversation()
        assert loader._current_path == str(path)
        assert loader._dirty is False

        loader.conversation_history.append(Message.user("追記"))
        loader._mark_dirty()
        loader._quick_save()
        assert len(json.loads(path.read_text(encoding="utf-8"))["conversation"]) == 3

    def test_clear_releases_the_target(self, window, monkeypatch,
                                       tmp_path, sample_history, auto_dialog):
        """
        上書き先を引き継いだままクリアすると、
        次の Ctrl+S で前の会話を空の内容で潰してしまう。
        """
        path = tmp_path / "ch1.json"
        choose_save(monkeypatch, path)
        window.conversation_history = list(sample_history)
        window._quick_save()

        auto_dialog(QMessageBox.Yes)
        window._clear_conversation()
        assert window._current_path is None
        assert window._dirty is False

        before = path.read_text(encoding="utf-8")
        window._quick_save()                              # 保存対象が無いので何もしない
        assert path.read_text(encoding="utf-8") == before

    def test_empty_conversation_is_not_saved(self, window):
        assert window._quick_save() is False


class TestCloseEvent:
    def _close(self, window):
        event = QCloseEvent()
        event.setAccepted(False)
        window.closeEvent(event)
        return event

    def test_empty_conversation_closes_silently(self, window):
        assert self._close(window).isAccepted()

    def test_saved_conversation_does_not_prompt(self, window, monkeypatch):
        asked = {"count": 0}

        def _counting(self, *args, **kwargs):
            asked["count"] += 1
            class _Stub:
                def button(self, *a):        return self
                def setText(self, *a):       pass
                def setDefaultButton(self, *a): pass
                def exec_(self):             return QMessageBox.No
            return _Stub()

        monkeypatch.setattr(GUI.OpenRouterChatApp, "_make_dialog", _counting)
        window.conversation_history = [Message.user("Q")]
        window._mark_dirty(False)

        assert self._close(window).isAccepted()
        assert asked["count"] == 0

    def test_unsaved_changes_prompt(self, window, auto_dialog, monkeypatch, tmp_path):
        auto_dialog(QMessageBox.Yes)
        choose_save(monkeypatch, tmp_path / "auto.json")
        window.conversation_history = [Message.user("Q")]
        window._mark_dirty(True)

        assert self._close(window).isAccepted()
        assert window._dirty is False                     # 保存も走っている

    def test_cancel_keeps_the_window_open(self, window, auto_dialog):
        auto_dialog(QMessageBox.Cancel)
        window.conversation_history = [Message.user("Q")]
        window._mark_dirty(True)
        assert not self._close(window).isAccepted()

    def test_failed_save_does_not_close(self, window, auto_dialog, monkeypatch):
        """
        保存したつもりで会話が消えるのを防ぐ。
        """
        auto_dialog(QMessageBox.Yes)
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: ("", "")))
        window.conversation_history = [Message.user("Q")]
        window._mark_dirty(True)
        assert not self._close(window).isAccepted()


class TestEditMode:
    def _edit(self, window, before, after):
        window._toggle_edit_mode()
        text = window.conversation_text.toPlainText().replace(before, after)
        window.conversation_text.setPlainText(text)
        window._toggle_edit_mode()
        return window.conversation_history

    def test_unchanged_text_keeps_all_metadata(self, window):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        window.conversation_history = [
            Message.user("元の質問"),
            Message.assistant("元の回答", model="x-ai/grok-4.3", reasoning="R",
                              usage={"completion_tokens": 9}),
        ]
        last = self._edit(window, "存在しない文字列", "x")[1]
        assert last.reasoning == "R"
        assert last.usage == {"completion_tokens": 9}
        assert last.edited is False

    def test_rewritten_text_drops_stale_metadata(self, window):
        """
        本文を書き換えたのに元の推論・使用量を残すと、その本文のものでない
        情報を提示してしまう。model だけ残して破棄する。
        """
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        window.conversation_history = [
            Message.user("元の質問"),
            Message.assistant("元の回答", model="x-ai/grok-4.3", reasoning="R",
                              usage={"completion_tokens": 9}),
        ]
        last = self._edit(window, "元の回答", "直した回答")[1]

        assert last.text == "直した回答"
        assert last.model == "x-ai/grok-4.3"     # どのモデル由来かは残す
        assert last.reasoning == ""              # 本文と対応しないので捨てる
        assert last.usage == {}
        assert last.edited is True
        assert "Grok: 直した回答" in window.conversation_text.toPlainText()

    def test_cannot_enter_edit_mode_while_streaming(self, window, monkeypatch):
        monkeypatch.setattr(GUI.ApiWorker, "start", lambda self: None)
        window.api_key = "key"
        window._start_request([{"role": "user", "content": []}])

        window._toggle_edit_mode()
        assert window.is_editing is False
        assert "応答中" in window.statusBar().currentMessage()
