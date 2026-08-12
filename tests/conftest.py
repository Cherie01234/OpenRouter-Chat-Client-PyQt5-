"""
テスト共通の下ごしらえ。

- GUI.py はリポジトリ直下にあるので import パスを通す
- ウィンドウを画面に出さずに動かす（offscreen）
- 実ユーザーの設定（QSettings）を読み書きしない
"""
import os
import sys
import time
from pathlib import Path

# GUI.py を import できるようにする（tests/ の 1 つ上）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# QApplication を作る前に設定しないと効かない
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 未設定だと起動時に「APIキー未設定」ダイアログが出て止まる
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-never-sent")

import pytest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QSettings

import GUI

TEST_SETTINGS_SCOPE = "OpenRouterChatTest"

# QThread を抱えたウィンドウを GC させるとプロセスごと落ちるため、
# セッション終了まで参照を持ち続ける
_LIVE_WINDOWS: list = []


# ══════════════════════════════════════════════════════════════
# 基本フィクスチャ
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def qapp():
    """QApplication はプロセスに 1 つだけ。"""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolated_settings():
    """テストが実ユーザーの設定を汚さないよう、専用スコープへ逃がす。"""
    GUI.OpenRouterChatApp._SETTINGS_ORG = TEST_SETTINGS_SCOPE
    GUI.OpenRouterChatApp._SETTINGS_APP = TEST_SETTINGS_SCOPE
    QSettings(TEST_SETTINGS_SCOPE, TEST_SETTINGS_SCOPE).clear()
    yield
    QSettings(TEST_SETTINGS_SCOPE, TEST_SETTINGS_SCOPE).clear()


@pytest.fixture(autouse=True)
def clean_catalog():
    """MODEL_CATALOG はグローバルなので、テスト毎に空へ戻す。"""
    GUI.MODEL_CATALOG.clear()
    yield GUI.MODEL_CATALOG
    GUI.MODEL_CATALOG.clear()


@pytest.fixture(autouse=True)
def fixed_model_configs(monkeypatch):
    """
    テスト中のモデル一覧を固定する。

    実際の一覧は models.local.json で差し替えられるうえ、入れ替えも頻繁に
    起きる。組み込みの定義に依存させると、モデルを変えるたびにテストが壊れる。
    """
    monkeypatch.setattr(GUI, "MODEL_CONFIGS", dict(TEST_MODEL_CONFIGS))
    yield TEST_MODEL_CONFIGS


@pytest.fixture
def make_window(qapp, monkeypatch, fixed_model_configs):
    """
    メインウィンドウを生成するファクトリ。

    モデル一覧の取得は既定で止める。テスト毎に通信させないため、
    またスレッドを増やさないため。
    """
    monkeypatch.setattr(
        GUI.OpenRouterChatApp, "_start_catalog_fetch", lambda self: None
    )

    def _make() -> GUI.OpenRouterChatApp:
        window = GUI.OpenRouterChatApp()
        _LIVE_WINDOWS.append(window)
        return window

    return _make


@pytest.fixture
def window(make_window):
    """起動直後のメインウィンドウ 1 枚。"""
    return make_window()


@pytest.fixture
def auto_dialog(monkeypatch):
    """
    確認ダイアログの戻り値を固定する。
    offscreen では誰も押せないので、押さないと止まってしまう。
    """
    def _answer(result):
        class _Stub:
            def button(self, *args):        return self
            def setText(self, *args):       pass
            def setDefaultButton(self, *a): pass
            def exec_(self):                return result
        monkeypatch.setattr(
            GUI.OpenRouterChatApp, "_make_dialog", lambda self, *a, **k: _Stub()
        )
    return _answer


# ══════════════════════════════════════════════════════════════
# 補助
# ══════════════════════════════════════════════════════════════

def wait_until(app, predicate, timeout: float = 5.0) -> bool:
    """
    条件が満たされるまでイベントループを回す。
    シグナルはキュー経由で届くので、processEvents しないと来ない。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


# テスト中に使うモデル一覧。GUI.py の組み込み定義とは切り離してある
# （実際の一覧は models.local.json で差し替えられ、入れ替えも頻繁に起きる）。
TEST_MODEL_CONFIGS = {
    "deepseek/deepseek-v4-pro": {
        "display_name": "DeepSeek", "color": "#7ec8a0",
        "supports_reasoning": True, "supports_thinking_level": True,
    },
    "deepseek/deepseek-v4-flash-0731": {
        "display_name": "DeepSeek Flash", "color": "#5aa87f",
        "supports_reasoning": True, "supports_thinking_level": True,
    },
    "openai/gpt-5.6-luna": {
        "display_name": "Luna", "color": "#c084fc",
        "supports_reasoning": True, "supports_thinking_level": True,
    },
}

# 実際の /api/v1/models の値に合わせてある（2026-08-02 時点）
CATALOG_FIXTURE = {
    "deepseek/deepseek-v4-pro": {
        "supports_reasoning": True, "supports_thinking_level": True,
        "reasoning_efforts": ["xhigh", "high"], "reasoning_default": "high",
        "context_length": 1_048_576, "max_completion_tokens": 384_000,
        "input_modalities": ["text"],
        "price_prompt": 4.3e-7, "price_completion": 8.7e-7,
    },
    "deepseek/deepseek-v4-flash-0731": {
        "supports_reasoning": True, "supports_thinking_level": True,
        "reasoning_efforts": ["max", "high", "low"], "reasoning_default": "high",
        "context_length": 1_048_576, "max_completion_tokens": 65_536,
        "input_modalities": ["text"],
        "price_prompt": 9e-8, "price_completion": 1.8e-7,
    },
    "openai/gpt-5.6-luna": {
        "supports_reasoning": True, "supports_thinking_level": True,
        "reasoning_efforts": ["max", "xhigh", "high", "medium", "low", "none"],
        "reasoning_default": "medium",
        "context_length": 1_050_000, "max_completion_tokens": 128_000,
        "input_modalities": ["file", "image", "text"],
        "price_prompt": 1e-7, "price_completion": 6e-7,
    },
}
