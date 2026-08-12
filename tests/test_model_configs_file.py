"""
モデル一覧をローカルファイルで差し替える仕組み。

使うモデルは人によって変わり、入れ替えも頻繁に起きる。GUI.py を直接
書き換えるとリポジトリと手元が分岐するため、gitignore したファイルで
上書きできるようにしてある。壊れていても起動は止めない。
"""
import json

import pytest

import GUI


def write(path, payload):
    path.write_text(
        payload if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False),
        encoding="utf-8")
    return str(path)


class TestLoading:
    def test_absent_file_uses_the_builtin_list(self, tmp_path):
        configs, warning = GUI.load_model_configs(str(tmp_path / "nope.json"))
        assert configs == GUI.DEFAULT_MODEL_CONFIGS
        assert warning == ""

    def test_file_replaces_the_builtin_list(self, tmp_path):
        path = write(tmp_path / "m.json", {
            "vendor/model-a": {"display_name": "A", "color": "#111111"},
            "vendor/model-b": {"display_name": "B", "color": "#222222"},
        })
        configs, warning = GUI.load_model_configs(path)

        assert warning == ""
        assert list(configs) == ["vendor/model-a", "vendor/model-b"]
        assert configs["vendor/model-a"]["display_name"] == "A"
        assert configs["vendor/model-b"]["color"] == "#222222"
        # 差し替えであって、組み込み分との併合ではない
        assert not set(GUI.DEFAULT_MODEL_CONFIGS) & set(configs)

    def test_missing_fields_get_defaults(self, tmp_path):
        path = write(tmp_path / "m.json", {"vendor/model-a": {}})
        configs, _ = GUI.load_model_configs(path)

        entry = configs["vendor/model-a"]
        assert entry["display_name"] == "model-a"          # ID の後半を使う
        assert entry["color"] == GUI.UNKNOWN_ASSISTANT_COLOR
        assert entry["supports_reasoning"] is True

    def test_thinking_level_can_be_disabled(self, tmp_path):
        """effort 指定に対応しないモデル用。"""
        path = write(tmp_path / "m.json", {
            "vendor/model-a": {"supports_thinking_level": False}})
        configs, _ = GUI.load_model_configs(path)
        assert configs["vendor/model-a"]["supports_thinking_level"] is False


class TestBrokenFileDoesNotStopStartup:
    @pytest.mark.parametrize("payload", [
        "{壊れたJSON",
        "[]",
        '"文字列"',
        "{}",
    ])
    def test_falls_back_with_an_explanation(self, tmp_path, payload):
        path = write(tmp_path / "m.json", payload)
        configs, warning = GUI.load_model_configs(path)

        assert configs == GUI.DEFAULT_MODEL_CONFIGS
        assert warning, "黙って既定へ戻ると、変更したつもりで気づけない"
        assert GUI.MODEL_CONFIGS_FILE in warning

    def test_invalid_entries_are_skipped_and_reported(self, tmp_path):
        path = write(tmp_path / "m.json", {
            "vendor/model-a": {"display_name": "A"},
            "スラッシュ無し": {"display_name": "X"},
        })
        configs, warning = GUI.load_model_configs(path)

        assert list(configs) == ["vendor/model-a"]
        assert "スラッシュ無し" in warning

    def test_non_object_entry_still_yields_a_usable_model(self, tmp_path):
        path = write(tmp_path / "m.json", {"vendor/model-a": "文字列"})
        configs, warning = GUI.load_model_configs(path)
        assert configs["vendor/model-a"]["display_name"] == "model-a"
        assert warning == ""


class TestWindowUsesTheLoadedList:
    def test_combo_lists_the_configured_models(self, window, fixed_model_configs):
        shown = [window.model_combo.itemText(i)
                 for i in range(window.model_combo.count())]
        assert shown == list(fixed_model_configs)

    def test_warning_is_surfaced_at_startup(self, make_window, monkeypatch):
        monkeypatch.setattr(GUI, "MODEL_CONFIGS_WARNING",
                            "models.local.json: 読めません")
        window = make_window()
        assert "モデル定義を読み込めませんでした" in \
            window.statusBar().currentMessage()

    def test_unknown_model_still_displays(self, window):
        """一覧から外したモデルの会話を開いても表示できること。"""
        message = GUI.Message.assistant("本文", model="vendor/retired-model")
        window.conversation_history = [message]
        window._redraw_conversation()
        assert "retired-model:" in window.conversation_text.toPlainText()
