"""
システムプロンプトのプリセット / 再生成 / コスト表示 / 添付。
"""
import os

import pytest
from PyQt5.QtWidgets import QInputDialog, QMessageBox
from PyQt5.QtCore import QMimeData, QUrl
from PyQt5.QtGui import QImage, QColor

import GUI
from GUI import Message
from conftest import CATALOG_FIXTURE


class TestSystemPromptPresets:
    @staticmethod
    def _name_dialog(monkeypatch, name, accepted=True):
        monkeypatch.setattr(QInputDialog, "getText",
                            staticmethod(lambda *a, **k: (name, accepted)))

    def test_save_and_reload(self, window, monkeypatch):
        window.system_prompt_input.setPlainText("回答は簡潔にまとめてください")
        self._name_dialog(monkeypatch, "簡潔モード")
        window._save_system_prompt_preset()

        assert window._load_presets() == {"簡潔モード": "回答は簡潔にまとめてください"}
        assert window.sp_preset_combo.findText("簡潔モード") > 0

    def test_persisted_across_instances(self, make_window, monkeypatch):
        first = make_window()
        first.system_prompt_input.setPlainText("設定A")
        self._name_dialog(monkeypatch, "A")
        first._save_system_prompt_preset()

        assert make_window()._load_presets() == {"A": "設定A"}

    def test_applying_fills_the_prompt(self, window, monkeypatch):
        window.system_prompt_input.setPlainText("設定A")
        self._name_dialog(monkeypatch, "A")
        window._save_system_prompt_preset()

        window.system_prompt_input.setPlainText("")
        index = window.sp_preset_combo.findText("A")
        window.sp_preset_combo.setCurrentIndex(index)
        window._apply_system_prompt_preset(index)
        assert window.system_prompt_input.toPlainText() == "設定A"

    def test_applying_over_existing_text_asks_first(self, window, monkeypatch, auto_dialog):
        window.system_prompt_input.setPlainText("設定A")
        self._name_dialog(monkeypatch, "A")
        window._save_system_prompt_preset()

        window.system_prompt_input.setPlainText("書きかけの内容")
        auto_dialog(QMessageBox.No)                     # 置き換えない
        index = window.sp_preset_combo.findText("A")
        window._apply_system_prompt_preset(index)
        assert window.system_prompt_input.toPlainText() == "書きかけの内容"

    def test_empty_prompt_is_not_saved(self, window, monkeypatch):
        window.system_prompt_input.setPlainText("   ")
        self._name_dialog(monkeypatch, "空")
        window._save_system_prompt_preset()
        assert window._load_presets() == {}

    def test_cancelled_name_dialog_saves_nothing(self, window, monkeypatch):
        window.system_prompt_input.setPlainText("設定")
        self._name_dialog(monkeypatch, "", accepted=False)
        window._save_system_prompt_preset()
        assert window._load_presets() == {}

    def test_delete(self, window, monkeypatch, auto_dialog):
        window.system_prompt_input.setPlainText("設定A")
        self._name_dialog(monkeypatch, "A")
        window._save_system_prompt_preset()

        auto_dialog(QMessageBox.Yes)
        window.sp_preset_combo.setCurrentIndex(window.sp_preset_combo.findText("A"))
        window._delete_system_prompt_preset()
        assert window._load_presets() == {}

    def test_placeholder_selection_does_nothing(self, window):
        window._apply_system_prompt_preset(0)
        assert window.system_prompt_input.toPlainText() == ""

    def test_corrupted_settings_are_ignored(self, window, monkeypatch):
        from PyQt5.QtCore import QSettings
        QSettings(window._SETTINGS_ORG, window._SETTINGS_APP).setValue(
            "system_prompt_presets", "{壊れたJSON"
        )
        assert window._load_presets() == {}


class TestRegenerate:
    def test_enabled_only_after_an_assistant_reply(self, window):
        window.conversation_history = [Message.user("質問")]
        window._update_usage_label()
        assert not window.regen_button.isEnabled()

        window.conversation_history.append(
            Message.assistant("回答", model="x-ai/grok-4.3"))
        window._update_usage_label()
        assert window.regen_button.isEnabled()

    def test_drops_the_last_reply_and_resends(self, window, monkeypatch):
        sent = {}
        monkeypatch.setattr(GUI.OpenRouterChatApp, "_start_request",
                            lambda self, messages: sent.update(messages=messages))
        window.conversation_history = [
            Message.user("続きを書いて"),
            Message.assistant("案1", model="x-ai/grok-4.3"),
        ]
        window._redraw_conversation()
        window._regenerate()

        assert [m.role for m in window.conversation_history] == ["user"]
        assert "案1" not in window.conversation_text.toPlainText()
        assert sent["messages"][-1]["content"][0]["text"] == "続きを書いて"

    def test_noop_when_nothing_to_regenerate(self, window):
        window.conversation_history = [Message.user("質問")]
        window._regenerate()
        assert len(window.conversation_history) == 1


class TestUsageLabel:
    def test_shows_tokens_cost_and_share(self, window):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        window.model_combo.setCurrentText("x-ai/grok-4.3")
        window.conversation_history = [
            Message.user("Q"),
            Message.assistant("A", model="x-ai/grok-4.3", usage={
                "prompt_tokens": 10_000, "completion_tokens": 2_000, "cost": 0.0215}),
        ]
        window._update_usage_label()

        text = window.usage_label.text()
        assert "12,000 tok" in text          # 次回の入力見込み = 入力 + 出力
        assert "1.2%" in text
        assert "次回入力" in text
        assert "セッション累計" in text

    def test_session_cost_is_summed(self, window):
        window.conversation_history = [
            Message.assistant("A", model="x-ai/grok-4.3", usage={"cost": 0.01}),
            Message.assistant("B", model="x-ai/grok-4.3", usage={"cost": 0.02}),
        ]
        assert window._session_cost() == pytest.approx(0.03)

    def test_no_measurement_yet(self, window):
        window._update_usage_label()
        assert "未計測" in window.usage_label.text()

    @pytest.mark.parametrize("amount,expected", [
        (0.00004, "$0.00004"),      # 少額でも 0 に潰さない
        (1.5,     "$1.500"),
    ])
    def test_cost_formatting(self, amount, expected):
        assert GUI._format_cost(amount) == expected


class TestAttachments:
    @staticmethod
    def _image_mime():
        image = QImage(32, 32, QImage.Format_RGB32)
        image.fill(QColor("red"))
        mime = QMimeData()
        mime.setImageData(image)
        return mime

    def test_clipboard_image_is_attached(self, window):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        window.model_combo.setCurrentText("x-ai/grok-4.3")

        assert window._accept_mime(self._image_mime()) is True
        assert len(window.selected_images) == 1
        assert window.selected_images[0][1] == "image/png"
        assert window.selected_images[0][2] == "clipboard-1.png"

    def test_repeated_pastes_are_numbered(self, window):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        window.model_combo.setCurrentText("x-ai/grok-4.3")
        window._accept_mime(self._image_mime())
        window._accept_mime(self._image_mime())
        assert window.selected_images[1][2] == "clipboard-2.png"

    def test_text_only_model_rejects_images(self, window):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        window.model_combo.setCurrentText("deepseek/deepseek-v4-pro")
        window._accept_mime(self._image_mime())
        assert window.selected_images == []

    def test_text_file_is_inserted_into_the_prompt(self, window, tmp_path):
        path = tmp_path / "設定資料.md"
        path.write_text("# 主人公\n\n名前: 未定\n", encoding="utf-8")

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        assert window._accept_mime(mime) is True

        body = window.message_input.toPlainText()
        assert "設定資料.md:" in body
        assert "名前: 未定" in body
        assert "```" in body

    def test_text_containing_a_fence_is_wrapped_safely(self, window, tmp_path):
        path = tmp_path / "code.md"
        path.write_text("```python\nx = 1\n```\n", encoding="utf-8")

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        window._accept_mime(mime)
        assert "````" in window.message_input.toPlainText()

    def test_oversized_text_file_is_rejected(self, window, tmp_path, auto_dialog):
        auto_dialog(QMessageBox.Ok)
        path = tmp_path / "big.txt"
        path.write_text("あ" * 400_000, encoding="utf-8")

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        window._accept_mime(mime)
        assert window.message_input.toPlainText() == ""

    def test_unsupported_extension_is_ignored(self, window):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile("C:/nowhere/app.exe")])
        assert window._accept_mime(mime) is False

    def test_empty_mime_is_ignored(self, window):
        assert window._accept_mime(QMimeData()) is False
