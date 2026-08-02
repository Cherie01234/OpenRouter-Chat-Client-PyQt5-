"""
外部レビューで指摘された不具合の回帰テスト。

いずれも「一見動いているが前提が誤っている」種類のもの。
"""
import json

import pytest
from PyQt5.QtWidgets import QFileDialog, QMessageBox

import GUI
from GUI import Message
from conftest import CATALOG_FIXTURE


class TestSanitizerSkipStack:
    """
    破棄中のタグを深さカウンタで数えると、閉じタグの対応を取れない。
    HTMLParser は開始・終了タグの整合を検証しないため、タグ名スタックが要る。
    """

    def test_void_drop_tag_does_not_swallow_the_rest(self):
        """
        <link> と <meta> には閉じタグが無い。カウンタ方式では戻らず、
        以降の本文がすべて消えていた（攻撃ではなく通常利用で起きる）。
        """
        assert "本文B" in GUI.sanitize_html("本文A<link><b>本文B</b>")
        assert "本文B" in GUI.sanitize_html('本文A<meta charset="x">本文B')

    def test_mismatched_end_tag_does_not_release_skip(self):
        out = GUI.sanitize_html("<iframe>秘密</embed><b>表示</b></iframe>")
        assert "表示" not in out
        assert "秘密" not in out

    def test_script_content_is_still_dropped(self):
        assert GUI.sanitize_html("<script>秘密</style><b>表示</b></script>") == ""

    def test_nested_drop_tags(self):
        out = GUI.sanitize_html("<iframe><object>秘密</object></iframe>後")
        assert "秘密" not in out and "後" in out

    def test_self_closing_drop_tag_does_not_swallow(self):
        assert "本文" in GUI.sanitize_html("<iframe/>本文")


class TestImageUrlAllowlist:
    @pytest.mark.parametrize("url,allowed", [
        ("data:image/png;base64,AAAA",      True),
        ("data:image/jpeg;base64,AAAA",     True),
        ("data:image/gif;base64,AAAA",      True),
        ("data:image/svg+xml;base64,AAAA",  False),   # 外部参照を含みうる
        ("https://evil.example/p.png",      False),
        ("file:///C:/secret.png",           False),
        ("",                                False),
    ])
    def test_is_safe_image_url(self, url, allowed):
        assert GUI.is_safe_image_url(url) is allowed

    def test_svg_is_rejected_by_the_sanitizer(self):
        assert "svg" not in GUI.sanitize_html(
            '<img src="data:image/svg+xml;base64,PHN2Zz4=">')

    def test_conversation_image_url_is_escaped(self, window):
        """
        会話ファイル由来の値を無検証で属性へ入れると、属性を閉じて
        別の（外部の）画像を差し込める。
        """
        crafted = 'data:image/png;base64,AAA" onload="x" src="https://evil.example/p.png'
        window.conversation_history = [Message("user", [
            {"type": "image_url", "image_url": {"url": crafted}}])]
        window._redraw_conversation()

        assert "evil.example" not in window.conversation_text.toHtml()


class TestSseParsing:
    """OpenRouter の途中エラーと終了理由は HTTP 200 の SSE として届く。"""

    @staticmethod
    def parse(lines):
        worker = GUI.ApiWorker("k", [], False, 0.7, 100, "deepseek/deepseek-v4-pro")
        got, usage, finish, error = [], {}, "", None
        worker.chunk_received.connect(got.append)
        worker.error.connect(lambda m: error or got and None)

        # run() の解析部と同じ順序を再現する
        for line in lines:
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            data = json.loads(line[6:])
            if data.get("error"):
                error = data["error"]
                break
            if data.get("usage"):
                usage = data["usage"]
            choices = data.get("choices") or []
            if not choices:
                continue
            if choices[0].get("finish_reason"):
                finish = choices[0]["finish_reason"]
        return usage, finish, error

    def test_usage_only_chunk_is_not_lost(self):
        """
        usage は choices が空のチャンクで届くことがある。
        choices を先に読むと IndexError で usage ごと落とす。
        """
        usage, _, _ = self.parse([
            'data: {"choices":[{"delta":{"content":"本文"}}]}',
            'data: {"choices":[],"usage":{"prompt_tokens":10,"cost":0.5}}',
        ])
        assert usage == {"prompt_tokens": 10, "cost": 0.5}

    def test_finish_reason_length_is_detected(self):
        _, finish, _ = self.parse([
            'data: {"choices":[{"delta":{"content":"途中"},"finish_reason":"length"}]}',
        ])
        assert finish == "length"

    def test_midstream_error_is_detected(self):
        _, _, error = self.parse([
            'data: {"choices":[{"delta":{"content":"途中"}}]}',
            'data: {"error":{"message":"upstream failed"}}',
        ])
        assert error and error["message"] == "upstream failed"


class TestResponseStatus:
    """終わり方を Message に残さないと、再描画・保存で消える。"""

    def test_truncated_is_recorded_and_shown(self, window):
        window._start_streaming_display("deepseek/deepseek-v4-pro")
        window._on_chunk_received("途中で切れた本文")
        window._flush_stream()
        window._on_stream_finished("", {}, GUI.STATUS_TRUNCATED)

        assert window.conversation_history[-1].status == GUI.STATUS_TRUNCATED
        assert "途切れています" in window.conversation_text.toPlainText()
        assert "最大トークン" in window.statusBar().currentMessage()

    def test_status_survives_redraw(self, window):
        window.conversation_history = [
            Message.assistant("部分", model="x-ai/grok-4.3",
                              status=GUI.STATUS_CANCELLED)]
        window._redraw_conversation()
        assert "（キャンセルされました）" in window.conversation_text.toPlainText()

    def test_status_survives_save_and_load(self, window, monkeypatch, tmp_path):
        path = tmp_path / "conv.json"
        window.conversation_history = [
            Message.assistant("部分", model="x-ai/grok-4.3",
                              status=GUI.STATUS_TRUNCATED)]
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(path), "")))
        window._save_conversation()

        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(path), "")))
        window._load_conversation()
        assert window.conversation_history[0].status == GUI.STATUS_TRUNCATED

    def test_completed_status_is_not_written(self):
        assert "status" not in Message.assistant("本文").to_json()


class TestDestructiveOperationsAreBlockedWhileStreaming:
    """
    応答中に文書を作り替えると、書き戻し位置の前提が壊れる。
    ボタンを無効化してもショートカットや直接呼び出しは防げない。
    """

    @pytest.fixture
    def streaming(self, window, monkeypatch):
        monkeypatch.setattr(GUI.ApiWorker, "start", lambda self: None)
        window.api_key = "key"
        window.conversation_history = [Message.user("Q")]
        window._start_request([{"role": "user", "content": []}])
        return window

    def test_clear_is_blocked(self, streaming):
        streaming._clear_conversation()
        assert streaming.conversation_history != []
        assert "応答中" in streaming.statusBar().currentMessage()

    def test_load_is_blocked(self, streaming, monkeypatch):
        called = []
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: called.append(1) or ("", "")))
        streaming._load_conversation()
        assert called == []

    def test_edit_mode_is_blocked(self, streaming):
        streaming._toggle_edit_mode()
        assert streaming.is_editing is False


class TestUnsavedGuard:
    def test_clear_asks_before_discarding(self, window, auto_dialog):
        window.conversation_history = [Message.user("大事な会話")]
        window._mark_dirty(True)
        auto_dialog(QMessageBox.No)

        window._clear_conversation()
        assert window.conversation_history != []

    def test_clear_proceeds_when_saved(self, window):
        window.conversation_history = [Message.user("保存済み")]
        window._mark_dirty(False)
        window._clear_conversation()
        assert window.conversation_history == []

    def test_load_asks_before_discarding(self, window, auto_dialog, monkeypatch):
        window.conversation_history = [Message.user("大事な会話")]
        window._mark_dirty(True)
        auto_dialog(QMessageBox.No)
        called = []
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: called.append(1) or ("", "")))
        window._load_conversation()
        assert called == []


class TestAtomicSave:
    def test_existing_file_survives_a_failed_write(self, window, monkeypatch, tmp_path):
        """直接上書きすると、書き込み途中の失敗で元の会話も失う。"""
        path = tmp_path / "conv.json"
        window.conversation_history = [Message.user("最初の内容")]
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(path), "")))
        assert window._save_conversation() is True
        original = path.read_text(encoding="utf-8")

        def _boom(*args, **kwargs):
            raise OSError("disk full")
        monkeypatch.setattr(json, "dump", _boom)
        monkeypatch.setattr(GUI.OpenRouterChatApp, "_make_dialog",
                            lambda self, *a, **k: type("D", (), {"exec_": lambda s: 0})())

        window.conversation_history.append(Message.user("追記"))
        assert window._write_conversation(str(path)) is False
        assert path.read_text(encoding="utf-8") == original      # 元ファイルは無事
        assert not (tmp_path / "conv.json.tmp").exists()         # 一時ファイルも残さない


class TestPerModelReasoningEfforts:
    """モデルごとに受け付ける effort は異なる。一律の選択肢は誤り。"""

    CATALOG = {
        "deepseek/deepseek-v4-pro": {
            **CATALOG_FIXTURE["deepseek/deepseek-v4-pro"],
            "reasoning_efforts": ["xhigh", "high"], "reasoning_default": "high"},
        "x-ai/grok-4.3": {
            **CATALOG_FIXTURE["x-ai/grok-4.3"],
            "reasoning_efforts": ["high", "medium", "low", "none"],
            "reasoning_default": "low"},
    }

    def test_parser_reads_the_reasoning_object(self):
        entry = GUI.parse_model_catalog({"data": [{
            "id": "a/b",
            "reasoning": {"supported_efforts": ["xhigh", "high"],
                          "default_effort": "high", "mandatory": False},
        }]})["a/b"]
        assert entry["reasoning_efforts"] == ["xhigh", "high"]
        assert entry["reasoning_default"] == "high"
        assert entry["supports_thinking_level"] is True

    def test_combo_lists_only_supported_efforts(self, window):
        window._on_catalog_loaded(self.CATALOG, True)

        window.model_combo.setCurrentText("deepseek/deepseek-v4-pro")
        items = [window.thinking_level_combo.itemText(i)
                 for i in range(window.thinking_level_combo.count())]
        assert items == ["xhigh", "high"]
        assert "minimal" not in items          # DeepSeek に minimal は無い

        window.model_combo.setCurrentText("x-ai/grok-4.3")
        items = [window.thinking_level_combo.itemText(i)
                 for i in range(window.thinking_level_combo.count())]
        assert items == ["high", "medium", "low", "none"]
        assert "xhigh" not in items            # Grok に xhigh は無い

    def test_falls_back_to_the_model_default(self, window):
        window._on_catalog_loaded(self.CATALOG, True)
        window.model_combo.setCurrentText("deepseek/deepseek-v4-pro")
        window.thinking_level_combo.setCurrentText("xhigh")

        window.model_combo.setCurrentText("x-ai/grok-4.3")   # xhigh は無い
        assert window.thinking_level_combo.currentText() == "low"

    def test_worker_only_sends_supported_effort(self):
        GUI.MODEL_CATALOG.update(self.CATALOG)
        config = GUI.get_model_config("x-ai/grok-4.3")
        worker = GUI.ApiWorker("k", [], True, 0.7, 100, "x-ai/grok-4.3",
                               "xhigh", model_config=config)
        # 対応しない effort は送らず、既定の有効化に落とす
        assert worker._build_reasoning_params() == {"reasoning": {"enabled": True}}


class TestPricingOverrides:
    """入力量に応じて単価が上がるモデルがある（例: 20万トークン超で2倍）。"""

    def test_parser_reads_overrides(self):
        entry = GUI.parse_model_catalog({"data": [{
            "id": "a/b",
            "pricing": {"prompt": "0.00000125",
                        "overrides": [{"min_prompt_tokens": 200000,
                                       "prompt": "0.0000025"}]},
        }]})["a/b"]
        assert entry["price_overrides"] == [(200000, 2.5e-6)]

    def test_price_switches_at_the_threshold(self):
        config = {"price_prompt": 1.25e-6, "price_overrides": [(200_000, 2.5e-6)]}
        assert GUI.prompt_price(config, 199_999) == pytest.approx(1.25e-6)
        assert GUI.prompt_price(config, 200_000) == pytest.approx(2.5e-6)

    def test_estimate_uses_the_higher_rate(self, window):
        GUI.MODEL_CATALOG.update({"x-ai/grok-4.3": {
            **CATALOG_FIXTURE["x-ai/grok-4.3"],
            "price_overrides": [(200_000, 2.5e-6)]}})
        window.model_combo.setCurrentText("x-ai/grok-4.3")
        window.conversation_history = [
            Message.assistant("A", model="x-ai/grok-4.3",
                              usage={"prompt_tokens": 250_000, "completion_tokens": 0})]
        window._update_usage_label()
        # 250,000 tok * 2.5e-6 = $0.625（上書き前なら $0.3125）
        assert "$0.625" in window.usage_label.text()


class TestCatalogSnapshot:
    def test_worker_keeps_the_config_it_started_with(self):
        """
        カタログは実行中に差し替わりうる。clear() と update() の間に
        読むと、空カタログとして推論設定が変わる。
        """
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        config = GUI.get_model_config("x-ai/grok-4.3")
        worker = GUI.ApiWorker("k", [], True, 0.7, 100, "x-ai/grok-4.3",
                               "high", model_config=config)
        GUI.MODEL_CATALOG.clear()
        assert worker._build_reasoning_params() == {"reasoning": {"effort": "high"}}


class TestRegenerateKeepsTheOldAnswer:
    """新しい応答が得られるまで、元の応答を失わないこと。"""

    @pytest.fixture
    def regenerating(self, window, monkeypatch):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        monkeypatch.setattr(GUI.ApiWorker, "start", lambda self: None)
        window.api_key = "key"
        window.conversation_history = [
            Message.user("続きを書いて"),
            Message.assistant("元の案", model="x-ai/grok-4.3", reasoning="R"),
        ]
        window._redraw_conversation()
        window._update_usage_label()
        window._regenerate()
        return window

    def test_old_answer_is_stashed_not_lost(self, regenerating):
        assert [m.role for m in regenerating.conversation_history] == ["user"]
        assert regenerating._regen_backup is not None

    def test_error_restores_the_old_answer(self, regenerating, auto_dialog):
        auto_dialog(0)                                   # 再試行しない
        regenerating._handle_api_error("APIエラー: 502")

        history = regenerating.conversation_history
        assert history[-1].text == "元の案"
        assert history[-1].reasoning == "R"
        assert "元の案" in regenerating.conversation_text.toPlainText()

    def test_cancel_restores_the_old_answer(self, regenerating):
        """
        中断した部分的な新案より、完成している元の応答を残す。
        """
        regenerating._on_chunk_received("書きかけ")
        regenerating._flush_stream()
        regenerating._on_stream_finished("", {}, GUI.STATUS_CANCELLED)

        history = regenerating.conversation_history
        assert history[-1].text == "元の案"
        shown = regenerating.conversation_text.toPlainText()
        assert "書きかけ" not in shown
        assert "元の案" in shown

    def test_success_replaces_the_old_answer(self, regenerating):
        regenerating._on_chunk_received("新しい案")
        regenerating._flush_stream()
        regenerating._on_stream_finished("", {}, GUI.STATUS_COMPLETED)

        history = regenerating.conversation_history
        assert history[-1].text == "新しい案"
        assert regenerating._regen_backup is None
        assert "元の案" not in regenerating.conversation_text.toPlainText()


class TestReasoningCheckboxLabel:
    def test_label_describes_what_it_controls(self, window):
        """
        実際は「reasoning パラメータを送るか」であり、表示制御ではない。
        """
        assert window.reasoning_checkbox.text() == "推論を要求"
        assert "パラメータ" in window.reasoning_checkbox.toolTip()


class TestEditModeRoundTrip:
    """
    旧実装は行頭の「あなた:」等で区切り、lstrip()/strip() をかけていた。
    そのため本文中の呼びかけで分割され、全角字下げと末尾の空行が消えていた。
    """

    def round_trip(self, window, text, role="assistant"):
        window.conversation_history = [
            Message(role, [{"type": "text", "text": text}],
                    model="x-ai/grok-4.3")]
        window._toggle_edit_mode()
        window._toggle_edit_mode()
        return window.conversation_history

    @pytest.mark.parametrize("text", [
        "　彼は立ち止まった。\n　雨が降っていた。\n\n",   # 全角字下げ + 末尾空行
        "    indented\n  two",                          # 半角字下げ
        "A\n\n\nB",                                     # 連続する空行
        "trailing   ",                                  # 末尾スペース
        "──── ここは本文 ────\n続き",                   # 区切りに似た行
    ])
    def test_text_is_preserved_exactly(self, window, text):
        history = self.round_trip(window, text)
        assert len(history) == 1
        assert history[0].text == text

    def test_second_person_line_does_not_split_the_message(self, window):
        text = "彼女は言った。\nあなた: と呼びかけられた気がした。\n続きの文。"
        history = self.round_trip(window, text)
        assert len(history) == 1                 # 旧実装では 2 件に割れていた
        assert history[0].role == "assistant"    # 後半が user 扱いにならない
        assert history[0].text == text

    def test_bulk_deletion_of_several_turns(self, window):
        """まとめて消して書き直す使い方を壊さないこと。"""
        window.conversation_history = [
            Message.user("Q1"), Message.assistant("A1", model="x-ai/grok-4.3"),
            Message.user("Q2"), Message.assistant("A2", model="x-ai/grok-4.3"),
            Message.user("Q3"), Message.assistant("A3", model="x-ai/grok-4.3"),
        ]
        window._toggle_edit_mode()
        document = window.conversation_text.toPlainText()
        head = document.split("──── メッセージ 3 ")[0].rstrip("\n")
        window.conversation_text.setPlainText(head)
        window._toggle_edit_mode()

        assert [m.text for m in window.conversation_history] == ["Q1", "A1"]
        assert window.conversation_history[1].model == "x-ai/grok-4.3"

    def test_rewriting_drops_stale_reasoning(self, window):
        window.conversation_history = [
            Message.assistant("元の回答", model="x-ai/grok-4.3", reasoning="R",
                              usage={"completion_tokens": 9})]
        window._toggle_edit_mode()
        window.conversation_text.setPlainText(
            window.conversation_text.toPlainText().replace("元の回答", "直した回答"))
        window._toggle_edit_mode()

        last = window.conversation_history[0]
        assert last.text == "直した回答"
        assert last.model == "x-ai/grok-4.3"
        assert last.reasoning == "" and last.usage == {}
        assert last.edited is True

    def test_deleting_all_delimiters_does_not_wipe_the_history(
            self, window, auto_dialog):
        """
        区切りを消した状態で取り込むと会話を丸ごと失う。中断して元に戻す。
        """
        auto_dialog(QMessageBox.Ok)
        window.conversation_history = [
            Message.user("大事な会話"),
            Message.assistant("大事な応答", model="x-ai/grok-4.3")]
        window._toggle_edit_mode()
        window.conversation_text.setPlainText("区切りを消してしまった本文")
        window._toggle_edit_mode()

        assert [m.text for m in window.conversation_history] == \
            ["大事な会話", "大事な応答"]

    def test_emptying_the_editor_does_not_wipe_the_history(self, window, auto_dialog):
        """
        全消しと「区切り行を壊した」は区別できない。
        会話を消したいときは確認の入る「クリア」を使ってもらう。
        """
        auto_dialog(QMessageBox.Ok)
        window.conversation_history = [Message.user("Q")]
        window._toggle_edit_mode()
        window.conversation_text.setPlainText("")
        window._toggle_edit_mode()
        assert [m.text for m in window.conversation_history] == ["Q"]

    def test_empty_history_stays_empty(self, window):
        """元から空なら、空のまま終了して構わない（警告も出さない）。"""
        window.conversation_history = []
        window._toggle_edit_mode()
        window._toggle_edit_mode()
        assert window.conversation_history == []


class TestImageLimits:
    @staticmethod
    def _png(path, side=8):
        from PyQt5.QtGui import QImage, QColor
        image = QImage(side, side, QImage.Format_RGB32)
        image.fill(QColor("red"))
        image.save(str(path), "PNG")
        return str(path)

    def test_count_is_capped(self, window, tmp_path, auto_dialog):
        auto_dialog(QMessageBox.Ok)
        path = self._png(tmp_path / "a.png")
        for _ in range(window.MAX_IMAGE_COUNT):
            assert window._attach_image_file(path) is True
        assert window._attach_image_file(path) is False
        assert len(window.selected_images) == window.MAX_IMAGE_COUNT

    def test_oversized_image_is_rejected(self, window, tmp_path, auto_dialog):
        auto_dialog(QMessageBox.Ok)
        path = tmp_path / "big.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * (window.MAX_IMAGE_BYTES + 1))
        assert window._attach_image_file(str(path)) is False

    def test_non_image_is_rejected_even_with_an_image_extension(
            self, window, tmp_path, auto_dialog):
        """
        拡張子だけを見ると、画像でないファイルを image/jpeg と偽って送る。
        """
        auto_dialog(QMessageBox.Ok)
        path = tmp_path / "fake.jpg"
        path.write_bytes(b"PK\x03\x04 this is a zip, not an image")
        assert window._attach_image_file(str(path)) is False
        assert window.selected_images == []

    def test_mime_comes_from_the_content(self, window, tmp_path):
        # 中身は PNG だが拡張子は .jpg
        path = self._png(tmp_path / "mislabeled.jpg")
        assert window._attach_image_file(path) is True
        assert window.selected_images[0][1] == "image/png"

    def test_label_shows_size_and_count(self, window, tmp_path):
        window._attach_image_file(self._png(tmp_path / "a.png"))
        window._update_image_info()
        label = window.image_info_label.text()
        assert f"/{window.MAX_IMAGE_COUNT} 枚" in label
        assert "毎ターン送信" in label


class TestResourceLoaderBlocksExternalFetch:
    """
    入口のサニタイズだけだと、HTML を差し込む経路が増えるたびに塞ぎ直しが要る。
    実際に読み込む直前で止める。
    """

    @pytest.mark.parametrize("url", [
        "https://evil.example/p.png",
        "http://evil.example/p.png",
        "file:///C:/secret.png",
        "C:/secret.png",
    ])
    def test_non_data_resources_are_refused(self, window, url):
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QTextDocument
        assert window.conversation_text.loadResource(
            QTextDocument.ImageResource, QUrl(url)) is None

    def test_embedded_data_images_still_render(self, window):
        """遮断しても、埋め込み画像の表示は保てること。"""
        import base64
        from PyQt5.QtCore import QBuffer
        from PyQt5.QtGui import QImage, QColor

        image = QImage(16, 16, QImage.Format_RGB32)
        image.fill(QColor("red"))
        buffer = QBuffer()
        buffer.open(QBuffer.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        url = "data:image/png;base64," + base64.b64encode(bytes(buffer.data())).decode()

        window.conversation_history = [Message("user", [
            {"type": "image_url", "image_url": {"url": url}}])]
        window._redraw_conversation()
        assert "<img" in window.conversation_text.toHtml()


class TestTextAttachmentFence:
    def test_fence_is_longer_than_any_run_in_the_body(self, window, tmp_path):
        path = tmp_path / "sample.md"
        path.write_text("````\nnested\n````\n", encoding="utf-8")
        window._insert_text_file(str(path))
        body = window.message_input.toPlainText()
        assert "`````" in body


class TestSessionAndPromptRestore:
    def test_system_prompt_round_trips(self, window, monkeypatch, tmp_path,
                                       auto_dialog):
        """
        保存しないと、別の会話を読み込んでも現在の設定のまま送ってしまう。
        """
        path = tmp_path / "conv.json"
        window.system_prompt_input.setPlainText("この会話専用の指示")
        window.conversation_history = [Message.user("Q")]
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(path), "")))
        window._save_conversation()

        # 変更は未保存扱いになるので、読み込み時に確認が入る
        window.system_prompt_input.setPlainText("別の指示")
        auto_dialog(QMessageBox.Yes)
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(path), "")))
        window._load_conversation()
        assert window.system_prompt_input.toPlainText() == "この会話専用の指示"

    def test_editing_the_system_prompt_marks_unsaved(self, window):
        """
        会話JSONへ保存する対象なので、変更は未保存として扱わないと、
        別会話を読み込んだ際に確認なしで失われる。
        """
        window.conversation_history = [Message.user("Q")]
        window._mark_dirty(False)
        window.system_prompt_input.setPlainText("後から変えた指示")
        assert window._dirty is True

    def test_session_start_is_restored(self, window, monkeypatch, tmp_path):
        path = tmp_path / "conv.json"
        path.write_text(json.dumps({
            "version": 2,
            "session_start": "2020-01-02T03:04:05",
            "conversation": [{"role": "user", "content": "Q"}],
        }), encoding="utf-8")
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(path), "")))
        window._load_conversation()
        assert window.session_start.year == 2020


class TestToggleStateSync:
    def test_edit_button_reflects_programmatic_toggle(self, window):
        window._toggle_edit_mode()
        assert window.edit_button.isChecked() is True
        window._toggle_edit_mode()
        assert window.edit_button.isChecked() is False

    def test_system_prompt_button_reflects_programmatic_toggle(self, window):
        window._toggle_system_prompt()
        assert window.sp_toggle.isChecked() is True
        window._toggle_system_prompt()
        assert window.sp_toggle.isChecked() is False


class TestInputWidthAlignment:
    def test_system_prompt_matches_the_message_input(self, window):
        window.system_prompt_input.setVisible(True)
        window.show()
        window.resize(1060, 720)

        def span(widget):
            left = widget.mapTo(window, widget.rect().topLeft()).x()
            return left, left + widget.width()

        assert span(window.system_prompt_input) == span(window.message_input)
