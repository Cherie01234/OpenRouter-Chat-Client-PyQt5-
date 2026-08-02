"""
会話データモデル（Message）。

model / reasoning / usage は表示と保存のための情報で、API へは送らない。
"""
import json

import GUI
from GUI import Message

from conftest import CATALOG_FIXTURE


class TestBasics:
    def test_user_content_is_normalized(self):
        assert Message.user("こんにちは").content == \
            [{"type": "text", "text": "こんにちは"}]

    def test_display_name_comes_from_the_recorded_model(self):
        assert Message.assistant("本文", model="openai/gpt-5.6-luna").display_name == "Luna"

    def test_color_comes_from_the_recorded_model(self):
        assert Message.assistant("本文", model="openai/gpt-5.6-luna").color == "#c084fc"

    def test_assistant_without_model_falls_back(self):
        assert Message("assistant", []).display_name == "アシスタント"

    def test_user_and_system_labels(self):
        assert Message.user("x").display_name == "あなた"
        assert Message.system("x").display_name == "システム"

    def test_text_joins_text_parts_only(self):
        message = Message("user", [
            {"type": "text", "text": "説明"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
            {"type": "text", "text": "続き"},
        ])
        assert message.text == "説明\n続き"


class TestApiPayload:
    def test_only_role_and_content_are_sent(self):
        message = Message.assistant("本文", model="openai/gpt-5.6-luna",
                                    reasoning="思考", usage={"cost": 1})
        assert set(message.to_api()) == {"role", "content"}

    def test_metadata_never_leaks_into_the_request(self):
        message = Message.assistant("本文", model="openai/gpt-5.6-luna", reasoning="秘密")
        assert "秘密" not in json.dumps(message.to_api(), ensure_ascii=False)

    def test_system_prompt_goes_first(self, window):
        window.system_prompt_input.setPlainText("あなたは校正者です")
        window.conversation_history = [Message.user("本文")]
        payload = window._build_api_messages()
        assert payload[0]["role"] == "system"
        assert all(set(m) == {"role", "content"} for m in payload)


class TestJsonRoundTrip:
    def test_round_trip_preserves_everything(self):
        original = Message.assistant("本文", model="openai/gpt-5.6-luna",
                                     reasoning="思考", usage={"cost": 0.5})
        restored = Message.from_json(json.loads(json.dumps(original.to_json())))
        assert (restored.role, restored.text, restored.model,
                restored.reasoning, restored.usage) == \
               (original.role, original.text, original.model,
                original.reasoning, original.usage)

    def test_empty_fields_are_omitted(self):
        assert "model" not in Message("user", []).to_json()

    def test_legacy_string_content_is_accepted(self):
        restored = Message.from_json({"role": "user", "content": "旧形式"})
        assert restored.content == [{"type": "text", "text": "旧形式"}]

    def test_missing_fields_get_defaults(self):
        restored = Message.from_json({})
        assert restored.role == "user" and restored.usage == {}


class TestModelIsRemembered:
    """
    以前は再描画時に「現在選択中のモデル名」で全発言を描いていたため、
    途中でモデルを変えると過去の発言まで名前が変わっていた。
    """

    def test_each_message_keeps_its_own_model(self, window):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        window.conversation_history = [
            Message.user("質問1"),
            Message.assistant("DeepSeekの回答", model="deepseek/deepseek-v4-pro"),
            Message.user("質問2"),
            Message.assistant("Lunaの回答", model="openai/gpt-5.6-luna"),
        ]
        window.model_combo.setCurrentText("deepseek/deepseek-v4-flash-0731")
        window._redraw_conversation()

        shown = window.conversation_text.toPlainText()
        assert "DeepSeek: DeepSeekの回答" in shown
        assert "Luna: Lunaの回答" in shown
        assert "DeepSeek Flash:" not in shown        # 現在の選択は混ざらない
