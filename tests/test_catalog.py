"""
モデルカタログ（OpenRouter /api/v1/models）。

表示名と色は手書き定義（MODEL_CONFIGS）、
能力・価格・コンテキスト長は API 由来を優先する。
"""
import json

import pytest

import GUI
from conftest import CATALOG_FIXTURE


SAMPLE_PAYLOAD = {"data": [{
    "id": "deepseek/deepseek-v4-flash-0731",
    "name": "DeepSeek Flash",
    "context_length": 1_048_576,
    "architecture": {"input_modalities": ["text", "image"]},
    "top_provider": {"max_completion_tokens": 65_536},
    "pricing": {"prompt": "0.0000005", "completion": "0.000003"},
    "supported_parameters": ["reasoning", "reasoning_effort", "max_tokens"],
}]}


class TestParsing:
    def test_capabilities_are_derived(self):
        entry = GUI.parse_model_catalog(SAMPLE_PAYLOAD)["deepseek/deepseek-v4-flash-0731"]
        assert entry["supports_reasoning"] is True
        assert entry["supports_thinking_level"] is True
        assert entry["context_length"] == 1_048_576
        assert entry["max_completion_tokens"] == 65_536
        assert entry["price_prompt"] == pytest.approx(5e-7)

    def test_empty_payload(self):
        assert GUI.parse_model_catalog({}) == {}

    def test_entries_without_id_are_skipped(self):
        assert GUI.parse_model_catalog({"data": [{"name": "no id"}]}) == {}

    def test_missing_sections_do_not_raise(self):
        entry = GUI.parse_model_catalog({"data": [{"id": "a/b"}]})["a/b"]
        assert entry["supports_reasoning"] is False
        assert entry["price_prompt"] is None
        assert entry["input_modalities"] == []

    def test_unparsable_price_becomes_none(self):
        payload = {"data": [{"id": "a/b", "pricing": {"prompt": "無料"}}]}
        assert GUI.parse_model_catalog(payload)["a/b"]["price_prompt"] is None


class TestMerge:
    def test_display_name_and_color_stay_hand_written(self):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        config = GUI.get_model_config("deepseek/deepseek-v4-flash-0731")
        assert config["display_name"] == "DeepSeek Flash"
        assert config["color"] == "#5aa87f"

    def test_capabilities_come_from_the_api(self):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        assert GUI.get_model_config(
            "deepseek/deepseek-v4-flash-0731")["max_completion_tokens"] == 65_536

    def test_unknown_model_does_not_raise(self):
        assert GUI.get_model_config("foo/bar")["display_name"] == "bar"

    def test_none_model_does_not_raise(self):
        assert GUI.get_model_config(None)["display_name"] == "アシスタント"

    def test_image_support_is_permissive_before_the_catalog_arrives(self):
        assert GUI.supports_images("deepseek/deepseek-v4-pro") is True
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        assert GUI.supports_images("deepseek/deepseek-v4-pro") is False


class TestUiFollowsCatalog:
    def test_max_tokens_ceiling_matches_the_model(self, window):
        window._on_catalog_loaded(CATALOG_FIXTURE, True)

        window.model_combo.setCurrentText("deepseek/deepseek-v4-flash-0731")
        assert window.max_tokens_spin.maximum() == 65_536

        window.model_combo.setCurrentText("deepseek/deepseek-v4-pro")
        assert window.max_tokens_spin.maximum() == 384_000

    def test_image_button_follows_the_model(self, window):
        window._on_catalog_loaded(CATALOG_FIXTURE, True)

        window.model_combo.setCurrentText("openai/gpt-5.6-luna")
        assert window.add_image_btn.isEnabled()

        window.model_combo.setCurrentText("deepseek/deepseek-v4-pro")
        assert not window.add_image_btn.isEnabled()
        assert "対応していません" in window.add_image_btn.toolTip()

    def test_switching_to_a_text_only_model_drops_attachments(self, window):
        window._on_catalog_loaded(CATALOG_FIXTURE, True)
        window.model_combo.setCurrentText("openai/gpt-5.6-luna")
        window.selected_images.append(("AAA", "image/png", "x.png"))

        window.model_combo.setCurrentText("deepseek/deepseek-v4-pro")
        assert window.selected_images == []

    def test_missing_models_are_reported(self, window):
        """MODEL_CONFIGS の ID が提供終了していたら気づけるようにする。"""
        window._on_catalog_loaded({"openai/gpt-5.6-luna": CATALOG_FIXTURE["openai/gpt-5.6-luna"]}, True)
        assert "未掲載" in window.statusBar().currentMessage()

    def test_failure_keeps_the_app_usable(self, window):
        window._on_catalog_failed("接続できません")
        assert "手書き定義" in window.statusBar().currentMessage()


class TestReasoningParameters:
    """
    OpenRouter の reasoning に level というフィールドは存在せず、
    effort と max_tokens は排他。effort に一本化してある。
    """

    @staticmethod
    def params(model, level, use_reasoning=True):
        worker = GUI.ApiWorker("key", [], use_reasoning, 0.7, 100, model, level)
        return worker._build_reasoning_params()

    def test_effort_is_sent(self):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        assert self.params("openai/gpt-5.6-luna", "xhigh") == {"reasoning": {"effort": "xhigh"}}

    def test_no_level_field(self):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        body = json.dumps(self.params("deepseek/deepseek-v4-flash-0731", "high"))
        assert "level" not in body

    def test_effort_and_max_tokens_are_not_combined(self):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        body = json.dumps(self.params("deepseek/deepseek-v4-flash-0731", "high"))
        assert "max_tokens" not in body

    def test_disabled_reasoning_sends_nothing(self):
        GUI.MODEL_CATALOG.update(CATALOG_FIXTURE)
        assert self.params("openai/gpt-5.6-luna", "high", use_reasoning=False) == {}

    def test_model_without_reasoning_sends_nothing(self):
        assert self.params("unknown/model", "high") == {}

    def test_xhigh_is_selectable(self):
        assert "xhigh" in GUI.THINKING_LEVELS


@pytest.mark.network
class TestRealEndpoint:
    """実際に OpenRouter へ問い合わせる。-m 'not network' で除外できる。"""

    def test_catalog_can_be_fetched(self, qapp, make_window, monkeypatch):
        monkeypatch.undo()          # _start_catalog_fetch の無効化を戻す
        window = GUI.OpenRouterChatApp()
        window._catalog_worker.wait(30_000)
        qapp.processEvents()
        assert len(GUI.MODEL_CATALOG) > 50
        window._catalog_worker = None
