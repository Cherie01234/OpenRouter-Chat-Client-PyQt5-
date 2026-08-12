"""
OpenRouter Chat v2 - 全改善案適用版
依存: pip install PyQt5 requests markdown
"""
import sys
import os
import json
import base64
import re
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLabel, QCheckBox, QSpinBox, QSplitter,
    QFrame, QMessageBox, QFileDialog, QComboBox, QDialog, QLineEdit,
    QShortcut, QAction, QInputDialog,
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QSettings, QTimer, QStandardPaths,
    QBuffer,
)
from PyQt5.QtGui import (
    QFont, QTextCursor, QPalette, QColor, QKeySequence, QTextDocument,
    QTextCharFormat, QImage, QImageReader,
)
import requests
import html
from html.parser import HTMLParser

try:
    import markdown as md_lib
    HAS_MARKDOWN = True
except ImportError:
    HAS_MARKDOWN = False

# ═══════════════════════════════════════════════════════════════
# モデル設定
# ═══════════════════════════════════════════════════════════════

USER_COLOR   = "#82b8e8"
SYSTEM_COLOR = "#e88080"
UNKNOWN_ASSISTANT_COLOR = "#e8e8e8"

# 表示名と色だけを手で定義する。推論の対応可否・コンテキスト長・出力上限・
# 価格・入力形式は、起動時に /api/v1/models から取得して上書きする。
# ここの supports_* は、カタログを取得できなかったときの控え。
DEFAULT_MODEL_CONFIGS: dict[str, dict] = {
    "deepseek/deepseek-v4-pro": {
        "display_name": "DeepSeek",
        "color": "#7ec8a0",          # 会話表示の送信者色
        "supports_reasoning": True,
        "supports_thinking_level": True,
    },
    "deepseek/deepseek-v4-flash-0731": {
        "display_name": "DeepSeek Flash",
        "color": "#5aa87f",          # Pro より少し濃い緑で区別
        "supports_reasoning": True,
        "supports_thinking_level": True,
    },
    "openai/gpt-5.6-luna": {
        "display_name": "Luna",
        "color": "#c084fc",
        "supports_reasoning": True,
        "supports_thinking_level": True,
    },
}

# 使うモデルは人によって変わるうえ、入れ替えも頻繁になる。
# このファイルを GUI.py の隣に置くと、上の定義の代わりに使う。
# .gitignore 済みなので、リポジトリと分岐させずに手元だけ変えられる。
MODEL_CONFIGS_FILE = "models.local.json"


def model_configs_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        MODEL_CONFIGS_FILE)


def load_model_configs(path: str | None = None) -> tuple[dict[str, dict], str]:
    """
    (モデル定義, 問題があればその説明) を返す。

    ファイルが壊れていても起動は止めない。組み込みの定義で動かしたうえで、
    黙って既定に戻ったと誤解されないよう、説明を呼び出し側へ返す。
    """
    path = path or model_configs_path()
    if not os.path.exists(path):
        return dict(DEFAULT_MODEL_CONFIGS), ""

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return dict(DEFAULT_MODEL_CONFIGS), f"{MODEL_CONFIGS_FILE}: {exc}"

    if not isinstance(data, dict):
        return dict(DEFAULT_MODEL_CONFIGS), \
            f"{MODEL_CONFIGS_FILE}: モデルIDをキーにしたオブジェクトにしてください"

    configs: dict[str, dict] = {}
    skipped: list[str] = []
    for model_id, entry in data.items():
        # OpenRouter のモデルIDは必ず "提供元/モデル名" の形
        if not isinstance(model_id, str) or "/" not in model_id:
            skipped.append(str(model_id))
            continue
        entry = entry if isinstance(entry, dict) else {}
        configs[model_id] = {
            "display_name": str(entry.get("display_name")
                                or model_id.split("/")[-1]),
            "color": str(entry.get("color") or UNKNOWN_ASSISTANT_COLOR),
            "supports_reasoning": bool(entry.get("supports_reasoning", True)),
            "supports_thinking_level": bool(
                entry.get("supports_thinking_level", True)),
        }

    if not configs:
        return dict(DEFAULT_MODEL_CONFIGS), \
            f"{MODEL_CONFIGS_FILE}: 有効なモデルがありません"
    if skipped:
        return configs, f"{MODEL_CONFIGS_FILE}: 無視した項目 {', '.join(skipped)}"
    return configs, ""


MODEL_CONFIGS, MODEL_CONFIGS_WARNING = load_model_configs()

# OpenRouter の reasoning.effort が受け付ける値（弱い順）。
# 旧実装は Gemini に存在しない "level" フィールドを送っていた。
# effort と max_tokens は排他なので、effort だけを使う。
THINKING_LEVELS: list[str] = ["minimal", "low", "medium", "high", "xhigh"]

# ═══════════════════════════════════════════════════════════════
# モデルカタログ（OpenRouter /api/v1/models から取得）
# ═══════════════════════════════════════════════════════════════

# 止まらないスレッドを抱えたまま打ち切ったときの終了コード。
# 通常終了（0）と区別できるようにする
FORCED_EXIT_CODE = 73

MODELS_URL         = "https://openrouter.ai/api/v1/models"
CATALOG_TTL_SECONDS = 24 * 60 * 60

# model_id → API 由来の能力情報。取得できるまでは空で、
# その間は MODEL_CONFIGS の手書き定義だけで動く。
MODEL_CATALOG: dict[str, dict] = {}


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_cost(amount: float) -> str:
    """少額でも 0 に潰れないよう桁数を切り替える。"""
    if amount and abs(amount) < 0.01:
        return f"${amount:.5f}"
    return f"${amount:.3f}"


def parse_model_catalog(payload: dict) -> dict[str, dict]:
    """/api/v1/models のレスポンスから、必要な能力情報だけ抜き出す。"""
    catalog: dict[str, dict] = {}
    for entry in payload.get("data") or []:
        model_id = entry.get("id")
        if not model_id:
            continue
        params    = set(entry.get("supported_parameters") or [])
        arch      = entry.get("architecture") or {}
        top       = entry.get("top_provider") or {}
        pricing   = entry.get("pricing") or {}
        reasoning = entry.get("reasoning") or {}

        # モデルごとに受け付ける effort は異なる。
        # 一律の選択肢を出すと、UI の表示と実際の挙動が食い違う
        efforts = [e for e in (reasoning.get("supported_efforts") or [])
                   if isinstance(e, str)]

        # 入力トークン数に応じた価格の切り替え（例: 20万トークン超で単価2倍）
        overrides = []
        for item in pricing.get("overrides") or []:
            threshold = item.get("min_prompt_tokens")
            price     = _to_float(item.get("prompt"))
            if isinstance(threshold, int) and price is not None:
                overrides.append((threshold, price))
        overrides.sort()

        catalog[model_id] = {
            "supports_reasoning":      bool(params & {"reasoning", "include_reasoning"}),
            "supports_thinking_level": bool(efforts) or "reasoning_effort" in params,
            "reasoning_efforts":       efforts,
            "reasoning_default":       reasoning.get("default_effort"),
            "reasoning_mandatory":     bool(reasoning.get("mandatory")),
            "context_length":          entry.get("context_length"),
            "max_completion_tokens":   top.get("max_completion_tokens"),
            "input_modalities":        list(arch.get("input_modalities") or []),
            "price_prompt":            _to_float(pricing.get("prompt")),
            "price_completion":        _to_float(pricing.get("completion")),
            "price_overrides":         overrides,
        }
    return catalog


def prompt_price(config: dict, tokens: int) -> float | None:
    """入力トークン数に応じた単価。閾値を超える上書き価格があれば適用する。"""
    price = config.get("price_prompt")
    for threshold, override in config.get("price_overrides") or []:
        if tokens >= threshold:
            price = override
    return price


def catalog_cache_path() -> str:
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) \
        or os.path.expanduser("~")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "model_catalog.json")


class ModelCatalogWorker(QThread):
    """モデル一覧の取得。起動を待たせないため別スレッドで動かす。"""
    loaded = pyqtSignal(dict, bool)   # (catalog, from_cache)
    failed = pyqtSignal(str)

    def run(self):
        path = catalog_cache_path()

        # 期限内のキャッシュがあれば通信しない
        try:
            age = datetime.now().timestamp() - os.path.getmtime(path)
            if age < CATALOG_TTL_SECONDS:
                with open(path, "r", encoding="utf-8") as f:
                    self.loaded.emit(parse_model_catalog(json.load(f)), True)
                    return
        except (OSError, json.JSONDecodeError):
            pass

        try:
            # このエンドポイントは認証不要。API キーは送らない
            response = requests.get(MODELS_URL, timeout=(10, 30))
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            # 取得できなくても手書き定義で動くので、致命的ではない
            self._emit_stale_cache_or_fail(path, str(exc))
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except OSError:
            pass

        self.loaded.emit(parse_model_catalog(payload), False)

    def _emit_stale_cache_or_fail(self, path: str, reason: str):
        """通信に失敗したら、期限切れのキャッシュでも使う。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.loaded.emit(parse_model_catalog(json.load(f)), True)
        except (OSError, json.JSONDecodeError):
            self.failed.emit(reason)

# ═══════════════════════════════════════════════════════════════
# スタイル定数
# ═══════════════════════════════════════════════════════════════

BUTTON_STYLE = """
    QPushButton {
        color: white;
        background-color: #2B5B84;
        border: 1px solid #1E415D;
        padding: 4px 8px;
        border-radius: 3px;
        font-family: "MS UI Gothic";
        min-width: 75px;
    }
    QPushButton:hover    { background-color: #3A7CBE; }
    QPushButton:pressed  { background-color: #1E415D; }
    QPushButton:checked  { background-color: #9B2626; }
    QPushButton:disabled { background-color: #555555; color: #888888; }
"""

INPUT_STYLE = """
    QTextEdit, QSpinBox, QComboBox, QLineEdit {
        color: black;
        background-color: white;
        border: 1px solid #CCCCCC;
        padding: 3px;
    }
    QTextEdit:focus, QSpinBox:focus, QComboBox:focus, QLineEdit:focus {
        border: 2px solid #2B5B84;
    }
"""

TEXT_AREA_STYLE = """
    QTextEdit {
        color: #e8e8e8;
        background-color: #1e1e1e;
        border: 1px solid #3a3a3a;
        selection-background-color: #2B5B84;
    }
    QTextEdit:editable {
        background-color: #2a2a2a;
        border: 2px solid #2B5B84;
    }
"""

# QMessageBox / QDialog 共通スタイル（ダークテーマ対応）
DIALOG_STYLE = """
    QDialog, QMessageBox {
        background-color: #2d2d2d;
    }
    QDialog QLabel, QMessageBox QLabel {
        color: #e8e8e8;
        font-size: 10pt;
    }
    QDialog QLineEdit {
        color: black;
        background-color: white;
        border: 1px solid #CCCCCC;
        padding: 4px;
        border-radius: 3px;
    }
    QPushButton {
        color: white;
        background-color: #2B5B84;
        border: 1px solid #1E415D;
        padding: 5px 14px;
        border-radius: 3px;
        min-width: 70px;
    }
    QPushButton:hover    { background-color: #3A7CBE; }
    QPushButton:pressed  { background-color: #1E415D; }
    QPushButton:default  { border: 2px solid #5a9fd4; }
    QPushButton:disabled { background-color: #555555; color: #888888; }
"""

# ═══════════════════════════════════════════════════════════════
# ユーティリティ
# ═══════════════════════════════════════════════════════════════

_CONFIG_DEFAULTS = {
    "supports_reasoning":      False,
    "supports_thinking_level": False,
    "reasoning_efforts":       [],
    "reasoning_default":       None,
    "reasoning_mandatory":     False,
    "context_length":          None,
    "max_completion_tokens":   None,
    "input_modalities":        ["text"],
    "price_prompt":            None,
    "price_completion":        None,
    "price_overrides":         [],
}


def get_model_config(model_id: str | None) -> dict:
    """
    モデルIDから設定を取得する。

    表示名と色は手書き定義（MODEL_CONFIGS）、
    能力・価格・コンテキスト長は API 由来（MODEL_CATALOG）を優先する。
    カタログ未取得でも手書き定義だけで動く。
    """
    model_id = model_id or ""
    config = dict(_CONFIG_DEFAULTS)
    config.update({
        "display_name": model_id.split("/")[-1] or "アシスタント",
        "color":        "#e88080",
    })
    config.update(MODEL_CONFIGS.get(model_id, {}))
    config.update(MODEL_CATALOG.get(model_id, {}))
    return config


def supports_images(model_id: str | None) -> bool:
    """カタログ未取得のうちは判定できないので、制限しない側に倒す。"""
    if model_id not in MODEL_CATALOG:
        return True
    return "image" in get_model_config(model_id)["input_modalities"]


def normalize_content(content) -> list[dict]:
    """
    conversation_history の content を常にリスト形式に正規化する。
    str  → [{"type": "text", "text": str}]
    list → そのまま
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": str(content)}]


def extract_text(content) -> str:
    """content からテキスト部分のみを結合して返す。"""
    return "\n".join(
        p.get("text", "")
        for p in normalize_content(content)
        if p.get("type") == "text"
    )


# ═══════════════════════════════════════════════════════════════
# 会話データモデル
# ═══════════════════════════════════════════════════════════════

# 保存フォーマットの版。1 = model/reasoning を持たない旧形式
CONVERSATION_FORMAT_VERSION = 2

# 応答の終わり方。表示と保存で区別する
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"    # 利用者が中断した
STATUS_TRUNCATED = "truncated"    # 出力上限に達して途中で切れた
STATUS_FILTERED  = "filtered"     # コンテンツフィルタで打ち切られた
STATUS_ERROR     = "error"        # finish_reason がエラーを示した

STATUS_NOTES = {
    STATUS_CANCELLED: "（キャンセルされました）",
    STATUS_TRUNCATED: "（最大トークンに達したため、ここで途切れています）",
    STATUS_FILTERED:  "（フィルタにより打ち切られました）",
    STATUS_ERROR:     "（エラーで終了したため、ここで途切れています）",
}


@dataclass
class Message:
    """
    会話1件。表示・保存に必要な情報をここに集約する。

    role/content 以外（model, reasoning, usage）は API へは送らない。
    送信用の最小形へ落とすのは to_api() の役目。
    """
    role: str                                   # user / assistant / system
    content: list[dict]                         # 常に list 形式
    model: str | None = None                    # assistant がどのモデルの応答か
    reasoning: str = ""
    usage: dict = field(default_factory=dict)
    timestamp: str = ""
    status: str = STATUS_COMPLETED              # 応答の終わり方
    edited: bool = False                        # 編集モードで本文を書き換えたか

    # ── 生成 ──────────────────────────────────────────────────

    @classmethod
    def user(cls, content) -> "Message":
        return cls("user", normalize_content(content), timestamp=_now())

    @classmethod
    def assistant(cls, text: str, model: str | None = None,
                  reasoning: str = "", usage: dict | None = None,
                  status: str = STATUS_COMPLETED) -> "Message":
        return cls("assistant", [{"type": "text", "text": text}],
                   model=model, reasoning=reasoning,
                   usage=usage or {}, timestamp=_now(), status=status)

    @classmethod
    def system(cls, text: str) -> "Message":
        return cls("system", [{"type": "text", "text": text}], timestamp=_now())

    # ── 表示 ──────────────────────────────────────────────────

    @property
    def text(self) -> str:
        return extract_text(self.content)

    @property
    def display_name(self) -> str:
        if self.role == "user":
            return "あなた"
        if self.role == "assistant":
            # 保存時のモデルを使う。現在選択中のモデル名で描かない
            return get_model_config(self.model)["display_name"] if self.model \
                else "アシスタント"
        return "システム"

    @property
    def color(self) -> str:
        if self.role == "user":
            return USER_COLOR
        if self.role == "assistant":
            return get_model_config(self.model)["color"] if self.model \
                else UNKNOWN_ASSISTANT_COLOR
        return SYSTEM_COLOR

    # ── 変換 ──────────────────────────────────────────────────

    def to_api(self) -> dict:
        """API 送信用の最小形。"""
        return {"role": self.role, "content": self.content}

    def to_json(self) -> dict:
        data = {"role": self.role, "content": self.content}
        if self.model:
            data["model"] = self.model
        if self.reasoning:
            data["reasoning"] = self.reasoning
        if self.usage:
            data["usage"] = self.usage
        if self.timestamp:
            data["timestamp"] = self.timestamp
        if self.status != STATUS_COMPLETED:
            data["status"] = self.status
        if self.edited:
            data["edited"] = True
        return data

    @classmethod
    def from_json(cls, data: dict) -> "Message":
        """旧形式（role/content のみ、content が str）も受け付ける。"""
        return cls(
            role      = data.get("role", "user"),
            content   = normalize_content(data.get("content", "")),
            model     = data.get("model") or None,
            reasoning = data.get("reasoning") or "",
            usage     = data.get("usage") or {},
            timestamp = data.get("timestamp") or "",
            status    = data.get("status") or STATUS_COMPLETED,
            edited    = bool(data.get("edited")),
        )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# QTextEdit へ埋め込んでよい画像形式。
# svg は外部リソースを参照しうるため除外する。
SAFE_IMAGE_MEDIATYPES = {
    "image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp",
}


def is_safe_image_url(url: str) -> bool:
    """埋め込み data: URI で、かつ許可した画像形式かどうか。"""
    value = (url or "").strip()
    if not value.lower().startswith("data:image/"):
        return False
    # 正規の data URI に引用符・空白・山括弧は現れない。
    # 混じっていれば属性を抜け出す細工とみなして弾く
    if any(ch in value for ch in '"\'<>' ) or any(ch.isspace() for ch in value):
        return False
    mediatype = value[len("data:"):].split(";", 1)[0].split(",", 1)[0]
    return mediatype.lower() in SAFE_IMAGE_MEDIATYPES


class _HtmlSanitizer(HTMLParser):
    """
    モデル出力由来の HTML から、外部リソースを取得しうる要素・属性を取り除く。

    QTextEdit は <img src="https://..."> を実際に取得しに行くため、素通しにすると
    「応答内容をモデルに指定された URL へ通知させる」経路になりうる。
    許可タグ以外はタグのみ剥がして中身のテキストは残す（unwrap）。
    """

    # 閉じタグを持たない要素。skip スタックへ積んではいけない
    # （積むと閉じタグが来ないまま以降の本文がすべて捨てられる）
    _VOID_TAGS = {
        "br", "hr", "img", "link", "meta", "input", "source",
        "col", "wbr", "area", "base", "param", "track", "embed",
    }

    _ALLOWED_TAGS = {
        "p", "br", "hr", "div", "span",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "ul", "ol", "li", "dl", "dt", "dd",
        "pre", "code", "blockquote",
        "strong", "b", "em", "i", "u", "s", "del", "ins", "sup", "sub",
        "table", "thead", "tbody", "tr", "th", "td",
        "a", "img",
    }

    # 中身ごと捨てるタグ
    _DROP_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "link", "meta"}

    _ALLOWED_ATTRS = {
        "a":    {"href"},
        "img":  {"src", "width", "height", "alt"},
        "code": {"class"},
        "pre":  {"class"},
        "th":   {"align"},
        "td":   {"align"},
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        # 破棄中のタグ名スタック。単なる深さカウンタだと、無関係な終了タグ
        # （<iframe>…</embed> など）で破棄が解除されてしまう
        self._skip_stack: list[str] = []

    def result(self) -> str:
        return "".join(self._out)

    @staticmethod
    def _is_safe_url(attr: str, value: str) -> bool:
        if attr == "src":
            return is_safe_image_url(value)
        return value.strip().lower().startswith(("http://", "https://", "mailto:"))

    def _clean_attrs(self, tag: str, attrs) -> str:
        allowed = self._ALLOWED_ATTRS.get(tag, frozenset())
        parts = []
        for name, value in attrs:
            name = (name or "").lower()
            if name not in allowed or value is None:
                continue
            if name in ("href", "src") and not self._is_safe_url(name, value):
                continue
            parts.append(f' {name}="{html.escape(value, quote=True)}"')
        return "".join(parts)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self._DROP_CONTENT_TAGS:
            # <link> や <meta> は閉じタグを持たないため、積むと戻らなくなる
            if tag not in self._VOID_TAGS:
                self._skip_stack.append(tag)
            return
        if self._skip_stack or tag not in self._ALLOWED_TAGS:
            return
        cleaned = self._clean_attrs(tag, attrs)
        if tag == "img" and "src=" not in cleaned:
            return          # src を落とした img は描画できないので要素ごと捨てる
        slash = "/" if tag in self._VOID_TAGS else ""
        self._out.append(f"<{tag}{cleaned}{slash}>")

    def handle_startendtag(self, tag, attrs):
        # <iframe/> のような自己終了形は中身を持たないので、積まずに捨てる
        if tag.lower() in self._DROP_CONTENT_TAGS:
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._skip_stack:
            # 対応する開始タグまで戻す。無関係な終了タグでは解除しない
            if tag in self._skip_stack:
                while self._skip_stack and self._skip_stack.pop() != tag:
                    pass
            return
        if tag not in self._ALLOWED_TAGS or tag in self._VOID_TAGS:
            return
        self._out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._skip_stack:
            self._out.append(html.escape(data, quote=False))


def sanitize_html(raw: str) -> str:
    """許可タグ・許可属性だけを残した HTML を返す。"""
    parser = _HtmlSanitizer()
    parser.feed(raw)
    parser.close()
    return parser.result()


def render_markdown(text: str) -> str:
    """
    Markdown → HTML 変換。
    markdown ライブラリがない場合はエスケープ + 改行変換のみ。

    単一改行の <br> 化は nl2br 拡張に任せる。
    （旧実装は全行末に半角スペース2つを付与していたため、
      フェンス内のコードにも末尾空白が混入していた）
    """
    if HAS_MARKDOWN:
        return sanitize_html(md_lib.markdown(
            text,
            extensions=["fenced_code", "tables", "nl2br"],
        ))
    return html.escape(text).replace("\n", "<br>")


# ═══════════════════════════════════════════════════════════════
# カスタムウィジェット
# ═══════════════════════════════════════════════════════════════

class MessageInput(QTextEdit):
    """
    Ctrl+Enter で送信シグナルを発火するメッセージ入力欄。
    画像・ファイルの貼り付けは media_pasted に流し、既定の挿入は行わない。
    """
    send_requested = pyqtSignal()
    media_pasted   = pyqtSignal(object)      # QMimeData

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() == Qt.ControlModifier):
            self.send_requested.emit()
        else:
            super().keyPressEvent(event)

    def canInsertFromMimeData(self, source) -> bool:
        if source.hasImage() or source.hasUrls():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        # 画像はテキストとして挿入せず、添付として扱う
        if source.hasImage() or source.hasUrls():
            self.media_pasted.emit(source)
            return
        super().insertFromMimeData(source)


class ConversationView(QTextEdit):
    """
    会話表示エリア。
    - Ctrl+ホイール: フォントサイズ変更
    - 右クリックメニュー: コピー機能拡張
    - 外部・ローカルのリソースは読み込まない（loadResource）
    """

    def loadResource(self, resource_type, url):
        """
        埋め込み data: 以外のリソースを一切読み込まない。

        QTextEdit は表示時にここを通して画像などを取りに行き、
        http/https なら通信し、ローカルパスならファイルを開く（実測確認済み）。
        入口のサニタイズだけで守ると、HTML を差し込む経路が増えるたびに
        塞ぎ直しが要る。実際に読む直前で止めれば、経路によらず効く。
        """
        if url.scheme().lower() != "data":
            return None
        return super().loadResource(resource_type, url)

    def wheelEvent(self, event):
        if event.modifiers() == Qt.ControlModifier:
            delta = event.angleDelta().y()
            font = self.font()
            new_size = max(8, min(24, font.pointSize() + (1 if delta > 0 else -1)))
            font.setPointSize(new_size)
            self.setFont(font)
            event.accept()
        else:
            super().wheelEvent(event)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        menu.setStyleSheet(DIALOG_STYLE)
        menu.addSeparator()

        copy_plain = QAction("選択テキストをプレーンコピー", self)
        copy_plain.setEnabled(self.textCursor().hasSelection())
        copy_plain.triggered.connect(self._copy_plain)
        menu.addAction(copy_plain)

        copy_all = QAction("全会話をコピー", self)
        copy_all.triggered.connect(
            lambda: QApplication.clipboard().setText(self.toPlainText())
        )
        menu.addAction(copy_all)

        menu.exec_(event.globalPos())

    def _copy_plain(self):
        cursor = self.textCursor()
        if cursor.hasSelection():
            QApplication.clipboard().setText(cursor.selectedText())


# ═══════════════════════════════════════════════════════════════
# 検索ダイアログ（Ctrl+F）
# ═══════════════════════════════════════════════════════════════

class SearchDialog(QDialog):
    """インライン検索ダイアログ。ラップアラウンド対応。"""

    def __init__(self, target: QTextEdit, parent=None):
        super().__init__(parent, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.target = target
        self.setWindowTitle("検索 (Ctrl+F)")
        self.setStyleSheet(DIALOG_STYLE)
        self.setFixedHeight(62)
        self.resize(420, 62)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("検索キーワード...")
        self.search_input.returnPressed.connect(self._search_next)
        layout.addWidget(self.search_input)

        prev_btn = QPushButton("◀ 前へ")
        prev_btn.setFixedWidth(72)
        prev_btn.clicked.connect(self._search_prev)

        next_btn = QPushButton("次へ ▶")
        next_btn.setFixedWidth(72)
        next_btn.clicked.connect(self._search_next)

        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(28)
        close_btn.clicked.connect(self.close)

        self.status_label = QLabel("")
        self.status_label.setMinimumWidth(80)

        layout.addWidget(prev_btn)
        layout.addWidget(next_btn)
        layout.addWidget(self.status_label)
        layout.addWidget(close_btn)

    def _search_next(self):
        self._do_search(QTextDocument.FindFlag(0))

    def _search_prev(self):
        self._do_search(QTextDocument.FindBackward)

    def _do_search(self, flag: QTextDocument.FindFlag):
        text = self.search_input.text()
        if not text:
            self.status_label.setText("")
            return

        found = self.target.find(text, flag)
        if not found:
            # ラップアラウンド
            cursor = self.target.textCursor()
            if flag == QTextDocument.FindBackward:
                cursor.movePosition(QTextCursor.End)
            else:
                cursor.movePosition(QTextCursor.Start)
            self.target.setTextCursor(cursor)
            found = self.target.find(text, flag)

        self.status_label.setText("" if found else "見つかりません")


# ═══════════════════════════════════════════════════════════════
# API ワーカー（ストリーミング対応）
# ═══════════════════════════════════════════════════════════════

class ApiWorker(QThread):
    # NOTE: シグナル名を "finished" にすると QThread 本来の finished シグナルを
    #       Python 側から覆い隠してしまい、スレッド終了の検知や
    #       deleteLater による後始末ができなくなる。必ず別名にすること。
    chunk_received    = pyqtSignal(str)             # ストリーミング差分テキスト
    response_finished = pyqtSignal(str, dict, str)  # (reasoning, usage, status)
    error             = pyqtSignal(str)

    URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key: str, messages: list, use_reasoning: bool,
                 temperature: float, max_tokens: int, model: str,
                 thinking_level: str = "medium", model_config: dict | None = None):
        super().__init__()
        self.api_key       = api_key
        self.messages      = messages
        self.use_reasoning = use_reasoning
        self.temperature   = temperature
        self.max_tokens    = max_tokens
        self.model         = model
        self.thinking_level = thinking_level
        # カタログはワーカー実行中に差し替わりうるので、開始時点の内容を持つ
        self.model_config  = model_config or get_model_config(model)

    # ── 中断 ──────────────────────────────────────────────────

    def cancel(self):
        """
        中断を要求する。フラグを立てるだけで、待たない・ソケットに触らない。

        実測（Windows）に基づく設計:
          - iter_lines() が受信待ちの間、中断フラグは読まれない。
            チャンクが流れている通常時は次の行で抜けるので即座に止まる。
          - 応答が完全に沈黙している場合は、リードタイムアウトまで抜けられない。
            close() も shutdown() も進行中の recv を解除できなかった。
          - しかも close() は呼び出し側スレッドをブロックしうるので、
            GUI スレッドから触ってはいけない。接続の解放は run() の finally に任せる。

        よって呼び出し側は、このスレッドの終了を待たずに UI を確定させること。
        """
        self.requestInterruption()

    # ── 推論パラメータ構築 ────────────────────────────────────

    def _build_reasoning_params(self) -> dict:
        """
        OpenRouter 共通の reasoning パラメータを組み立てる。

        仕様上 effort と max_tokens は排他で、level というフィールドは存在しない。
        effort に一本化する（未対応モデルには enabled だけ送る）。
        """
        cfg = self.model_config
        if not self.use_reasoning or not cfg["supports_reasoning"]:
            return {}
        allowed = cfg.get("reasoning_efforts") or THINKING_LEVELS
        if cfg["supports_thinking_level"] and self.thinking_level in allowed:
            return {"reasoning": {"effort": self.thinking_level}}
        return {"reasoning": {"enabled": True}}

    # ── ストリーミング実行 ────────────────────────────────────

    def run(self):
        if self.isInterruptionRequested():
            return

        response       = None
        full_reasoning = ""
        usage: dict    = {}
        finish_reason  = ""

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type":  "application/json",
            }
            body = {
                "model":       self.model,
                "messages":    self.messages,
                "temperature": self.temperature,
                "max_tokens":  self.max_tokens,
                "stream":      True,
                **self._build_reasoning_params(),
            }

            response = requests.post(
                self.URL, headers=headers, json=body,
                timeout=(10, 120), stream=True,
            )

            if response.status_code != 200:
                self.error.emit(
                    f"APIエラー: {response.status_code} - {response.text}"
                )
                return

            for raw_line in response.iter_lines():
                if self.isInterruptionRequested():
                    break
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8")
                if line == "data: [DONE]":
                    break
                if not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                # 途中エラーは HTTP 200 の SSE として届く。
                # 見落とすと部分本文を「完了」として扱ってしまう
                err = data.get("error")
                if err:
                    message = err.get("message") if isinstance(err, dict) else str(err)
                    self.error.emit(f"APIエラー（応答の途中）: {message}")
                    return

                # usage は choices が空のチャンクで届くことがある。
                # choices を先に読むと IndexError で usage ごと落とす
                if data.get("usage"):
                    usage = data["usage"]

                choices = data.get("choices") or []
                if not choices:
                    continue
                choice = choices[0] or {}
                delta  = choice.get("delta") or {}

                # コンテンツチャンク
                chunk = delta.get("content") or ""
                if chunk:
                    self.chunk_received.emit(chunk)

                # 推論テキストの収集
                for key in ("reasoning", "reasoning_content", "reasoning_text"):
                    r = delta.get(key, "") or ""
                    if r:
                        full_reasoning += r
                        break

                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

        except requests.exceptions.Timeout:
            if not self.isInterruptionRequested():
                self.error.emit(
                    "タイムアウト: サーバーからの応答がありません。再試行してください。"
                )
            return
        except Exception as exc:
            # 中断済みなら、利用者が止めた結果の例外なのでエラー扱いしない
            if not self.isInterruptionRequested():
                self.error.emit(f"例外が発生しました: {exc}")
            return
        finally:
            if response is not None:
                try:
                    response.close()      # 接続を確実に解放する（自スレッド内で）
                except Exception:
                    pass

        # 推論ヘッダーの付加
        if full_reasoning:
            cfg = self.model_config
            r_tok = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") \
                or usage.get("reasoning_tokens", 0)
            if r_tok:
                full_reasoning = (
                    f"【推論トークン使用量: {r_tok}】\n\n" + full_reasoning
                )
            if cfg["supports_thinking_level"]:
                full_reasoning = (
                    f"【思考レベル: {self.thinking_level}】\n\n" + full_reasoning
                )

        if self.isInterruptionRequested():
            status = STATUS_CANCELLED
        elif finish_reason == "length":
            status = STATUS_TRUNCATED       # 出力上限に達して途中で切れた
        elif finish_reason == "content_filter":
            status = STATUS_FILTERED
        elif finish_reason == "error":
            # 正式な途中エラーはトップレベル error で先に処理される。
            # ここへ来るのは finish_reason だけがエラーを示した場合
            status = STATUS_ERROR
        else:
            status = STATUS_COMPLETED

        self.response_finished.emit(full_reasoning, usage, status)


# ═══════════════════════════════════════════════════════════════
# メインウィンドウ
# ═══════════════════════════════════════════════════════════════

class OpenRouterChatApp(QMainWindow):
    _SETTINGS_ORG = "OpenRouterChat"
    _SETTINGS_APP = "OpenRouterChat"

    def __init__(self):
        super().__init__()
        self.api_key: str | None = os.getenv("OPENROUTER_API_KEY")

        # 会話履歴
        self.conversation_history: list[Message] = []
        self.session_start = datetime.now()

        self.is_editing   = False
        self.selected_images: list[tuple[str, str, str]] = []
        self.worker: ApiWorker | None = None
        # 中断要求後もしばらく生き残るワーカーの置き場（GC 防止）
        self._retired_workers: list[ApiWorker] = []
        # リクエスト開始から応答確定まで True。
        # スレッドの生存だけで判定すると、run() が終わってから
        # 完了シグナルが届くまでの間だけ「空き」に見えてしまう
        self._request_active = False

        # ストリーミング状態
        self._stream_buffer        = ""
        self._stream_pending       = ""  # 未描画のチャンク（タイマーでまとめて反映）
        self._stream_message_start = 0   # 送信者プレフィックス前の位置
        self._stream_content_start = 0   # 送信者プレフィックス後（本文開始）の位置
        self._stream_placeholder   = False
        self._stream_model: str | None = None  # この応答を生成したモデル

        # チャンク毎に文書を再レイアウトすると長文で重くなるため、
        # 一定間隔でまとめて描画する
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(60)
        self._stream_timer.timeout.connect(self._flush_stream)

        # リトライ用
        self._last_api_messages: list[dict] = []
        # 再生成中に退避しておく元の応答（失敗したら戻す）
        self._regen_backup: Message | None = None
        # 直前に送った user 発言（0チャンクで中断したら取り下げる）
        self._pending_user_message: Message | None = None

        # 保存状態（Ctrl+S の上書き先と、未保存変更の有無）
        self._current_path: str | None = None
        self._dirty = False
        # 終了時に止まりきらなかったスレッドが残ったか（main() が終了方法を選ぶ）
        self.threads_pending = False

        # 検索ダイアログ
        self._search_dialog: SearchDialog | None = None

        # モデルカタログ取得スレッド
        self._catalog_worker: ModelCatalogWorker | None = None

        self._init_ui()
        self._load_settings()
        self._start_catalog_fetch()

    # ══════════════════════════════════════════════════════════
    # UI 構築
    # ══════════════════════════════════════════════════════════

    def _init_ui(self):
        self._update_window_title()
        self.setGeometry(100, 100, 1060, 800)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(4)
        root.setContentsMargins(6, 6, 6, 6)

        root.addWidget(self._build_conversation_area())    # スプリッタ
        root.addWidget(self._build_system_prompt_area())   # 折りたたみ式SP欄
        root.addWidget(self._build_input_area())           # 入力エリア全体
        self._align_input_widths()

        # ショートカット
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._open_search)
        QShortcut(QKeySequence.Save,      self).activated.connect(self._quick_save)
        QShortcut(QKeySequence.SaveAs,    self).activated.connect(self._save_conversation)

        # 画像・テキストのドロップを受け付ける
        self.setAcceptDrops(True)

        # 初期状態を整える
        self._refresh_preset_combo()
        self._on_model_changed(self.model_combo.currentText())
        self._update_usage_label()

        # モデル定義ファイルに問題があれば知らせる。
        # 黙って既定のリストに戻ると、変更したつもりで気づけない
        if MODEL_CONFIGS_WARNING:
            self.statusBar().showMessage(
                f"モデル定義を読み込めませんでした（既定を使用）: "
                f"{MODEL_CONFIGS_WARNING}"
            )

        # APIキー未設定
        if not self.api_key:
            self.send_button.setEnabled(False)
            self._make_dialog(
                "APIキー未設定",
                "OPENROUTER_API_KEY 環境変数が設定されていません。\n"
                "設定後、アプリケーションを再起動してください。",
            ).exec_()

    # ── 会話エリア ──────────────────────────────────────────

    def _build_conversation_area(self) -> QSplitter:
        self.conversation_text = ConversationView()
        self.conversation_text.setReadOnly(True)
        self.conversation_text.setFont(QFont("Arial", 10))
        self.conversation_text.setStyleSheet(TEXT_AREA_STYLE)

        self.reasoning_text = ConversationView()
        self.reasoning_text.setReadOnly(True)
        self.reasoning_text.setFont(QFont("Arial", 9))
        self.reasoning_text.setMaximumHeight(160)
        self.reasoning_text.setStyleSheet(TEXT_AREA_STYLE)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.conversation_text)
        splitter.addWidget(self.reasoning_text)
        splitter.setSizes([580, 160])
        return splitter

    # ── システムプロンプト（折りたたみ） ─────────────────────

    def _build_system_prompt_area(self) -> QFrame:
        frame = QFrame()
        self.sp_frame = frame
        self._sp_expanded = False
        layout = QVBoxLayout(frame)
        # 左右の余白は _align_input_widths() で入力欄に合わせて上書きする
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(2)

        self.sp_toggle = self._make_button(
            "▶ システムプロンプト", self._toggle_system_prompt, checkable=True
        )
        self.sp_toggle.setFixedHeight(22)
        layout.addWidget(self.sp_toggle)

        # ── プリセット行 ──────────────────────────────────────
        self.sp_preset_row = QWidget()
        preset_layout = QHBoxLayout(self.sp_preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(4)

        preset_layout.addWidget(QLabel("プリセット:"))
        self.sp_preset_combo = QComboBox()
        self.sp_preset_combo.setMinimumWidth(180)
        self.sp_preset_combo.setStyleSheet(INPUT_STYLE)
        self.sp_preset_combo.activated.connect(self._apply_system_prompt_preset)
        preset_layout.addWidget(self.sp_preset_combo)

        self.sp_save_btn   = self._make_button("保存", self._save_system_prompt_preset)
        self.sp_delete_btn = self._make_button("削除", self._delete_system_prompt_preset)
        for btn in (self.sp_save_btn, self.sp_delete_btn):
            btn.setFixedHeight(22)
            btn.setMinimumWidth(52)
            preset_layout.addWidget(btn)
        preset_layout.addStretch()

        self.sp_preset_row.setVisible(False)
        layout.addWidget(self.sp_preset_row)

        self.system_prompt_input = QTextEdit()
        self.system_prompt_input.setPlaceholderText(
            "モデルへの事前指示を入力（任意）..."
        )
        self.system_prompt_input.setMaximumHeight(70)
        self.system_prompt_input.setStyleSheet(INPUT_STYLE)
        self.system_prompt_input.setVisible(False)
        # 会話JSONへ保存する対象なので、変更は未保存として扱う
        self.system_prompt_input.textChanged.connect(self._mark_dirty)
        layout.addWidget(self.system_prompt_input)

        return frame

    def _align_input_widths(self):
        """
        システムプロンプト欄の左右をメッセージ入力欄に揃える。

        入力欄は枠付き QFrame の内側にあるのに対し、システムプロンプト欄は
        ルート直下に置かれているため、放っておくと枠と内側余白の分だけ
        システムプロンプト欄のほうが横に長くなる。
        余白の既定値はスタイルによって変わるので、実際の値から算出する。
        """
        inset = self.input_frame.frameWidth() \
            + self.input_frame.layout().contentsMargins().left()
        margins = self.sp_frame.layout().contentsMargins()
        self.sp_frame.layout().setContentsMargins(
            inset, margins.top(), inset, margins.bottom()
        )

    def _toggle_system_prompt(self):
        self._set_system_prompt_expanded(not self._sp_expanded)

    def _set_system_prompt_expanded(self, expanded: bool):
        """
        開閉状態は自前で持つ。isVisible() は親ウィンドウが未表示のあいだ
        常に False を返すため、判定の根拠に使えない。
        """
        self._sp_expanded = expanded
        self.system_prompt_input.setVisible(expanded)
        self.sp_preset_row.setVisible(expanded)
        self.sp_toggle.setChecked(expanded)     # コードから呼んだ場合に備える
        self.sp_toggle.setText(
            "▼ システムプロンプト" if expanded else "▶ システムプロンプト"
        )

    # ── システムプロンプトのプリセット ────────────────────────

    _PRESET_PLACEHOLDER = "（選択してください）"

    def _load_presets(self) -> dict[str, str]:
        raw = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP) \
            .value("system_prompt_presets", "")
        try:
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _store_presets(self, presets: dict[str, str]):
        QSettings(self._SETTINGS_ORG, self._SETTINGS_APP).setValue(
            "system_prompt_presets", json.dumps(presets, ensure_ascii=False)
        )

    def _refresh_preset_combo(self, select: str | None = None):
        self.sp_preset_combo.blockSignals(True)
        self.sp_preset_combo.clear()
        self.sp_preset_combo.addItem(self._PRESET_PLACEHOLDER)
        for name in sorted(self._load_presets()):
            self.sp_preset_combo.addItem(name)
        if select:
            index = self.sp_preset_combo.findText(select)
            if index >= 0:
                self.sp_preset_combo.setCurrentIndex(index)
        self.sp_preset_combo.blockSignals(False)

    def _apply_system_prompt_preset(self, index: int):
        name = self.sp_preset_combo.itemText(index)
        if index <= 0:
            return
        presets = self._load_presets()
        if name not in presets:
            return
        current = self.system_prompt_input.toPlainText().strip()
        if current and current != presets[name]:
            box = self._make_dialog(
                "確認",
                f"現在のシステムプロンプトを「{name}」で置き換えますか？",
                QMessageBox.Yes | QMessageBox.No,
            )
            box.button(QMessageBox.Yes).setText("置き換える")
            box.button(QMessageBox.No).setText("やめる")
            if box.exec_() != QMessageBox.Yes:
                self._refresh_preset_combo()
                return
        self.system_prompt_input.setPlainText(presets[name])
        self.statusBar().showMessage(f"プリセットを適用しました: {name}")

    def _save_system_prompt_preset(self):
        text = self.system_prompt_input.toPlainText().strip()
        if not text:
            self.statusBar().showMessage("システムプロンプトが空です")
            return
        current = self.sp_preset_combo.currentText()
        suggested = current if current != self._PRESET_PLACEHOLDER else ""
        name, ok = QInputDialog.getText(
            self, "プリセットを保存", "名前:", QLineEdit.Normal, suggested
        )
        name = name.strip()
        if not ok or not name:
            return
        presets = self._load_presets()
        if name in presets and presets[name] != text:
            box = self._make_dialog(
                "確認", f"「{name}」は既にあります。上書きしますか？",
                QMessageBox.Yes | QMessageBox.No,
            )
            box.button(QMessageBox.Yes).setText("上書き")
            box.button(QMessageBox.No).setText("やめる")
            if box.exec_() != QMessageBox.Yes:
                return
        presets[name] = text
        self._store_presets(presets)
        self._refresh_preset_combo(select=name)
        self.statusBar().showMessage(f"プリセットを保存しました: {name}")

    def _delete_system_prompt_preset(self):
        name = self.sp_preset_combo.currentText()
        if name == self._PRESET_PLACEHOLDER:
            self.statusBar().showMessage("削除するプリセットを選んでください")
            return
        box = self._make_dialog(
            "確認", f"プリセット「{name}」を削除しますか？",
            QMessageBox.Yes | QMessageBox.No,
        )
        box.button(QMessageBox.Yes).setText("削除")
        box.button(QMessageBox.No).setText("やめる")
        if box.exec_() != QMessageBox.Yes:
            return
        presets = self._load_presets()
        presets.pop(name, None)
        self._store_presets(presets)
        self._refresh_preset_combo()
        self.statusBar().showMessage(f"プリセットを削除しました: {name}")

    # ── 入力エリア全体 ────────────────────────────────────────

    def _build_input_area(self) -> QFrame:
        frame = QFrame()
        self.input_frame = frame
        frame.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(frame)
        layout.setSpacing(4)
        layout.addWidget(self._build_message_input())
        layout.addLayout(self._build_image_panel())
        layout.addLayout(self._build_settings_panel())
        layout.addWidget(self._build_usage_label())
        layout.addLayout(self._build_button_panel())
        return frame

    def _build_usage_label(self) -> QLabel:
        self.usage_label = QLabel("")
        self.usage_label.setStyleSheet("color: #9a9a9a;")
        self.usage_label.setFont(QFont("MS UI Gothic", 8))
        return self.usage_label

    def _build_message_input(self) -> MessageInput:
        self.message_input = MessageInput()
        self.message_input.setPlaceholderText(
            "メッセージを入力 … (Ctrl+Enter で送信)"
        )
        self.message_input.setMaximumHeight(80)
        self.message_input.setStyleSheet(INPUT_STYLE)
        self.message_input.send_requested.connect(self._send_message)
        self.message_input.media_pasted.connect(self._accept_mime)
        return self.message_input

    def _build_image_panel(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        self.add_image_btn    = self._make_button("画像を追加",   self._add_image)
        self.clear_images_btn = self._make_button("画像をクリア", self._clear_images)
        self.image_info_label = QLabel("選択された画像: なし")
        layout.addWidget(self.add_image_btn)
        layout.addWidget(self.clear_images_btn)
        layout.addWidget(self.image_info_label)
        layout.addStretch()
        return layout

    def _build_settings_panel(self) -> QHBoxLayout:
        layout = QHBoxLayout()

        layout.addWidget(QLabel("モデル:"))
        self.model_combo = QComboBox()
        self.model_combo.addItems(list(MODEL_CONFIGS.keys()))
        self.model_combo.setStyleSheet(INPUT_STYLE)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        layout.addWidget(self.model_combo)

        layout.addWidget(QLabel("思考レベル:"))
        self.thinking_level_combo = QComboBox()
        self.thinking_level_combo.addItems(THINKING_LEVELS)
        self.thinking_level_combo.setCurrentText("medium")
        self.thinking_level_combo.setStyleSheet(INPUT_STYLE)
        self.thinking_level_combo.setEnabled(False)
        layout.addWidget(self.thinking_level_combo)

        # 実際に制御しているのは「reasoning パラメータを送るかどうか」で、
        # 推論欄の表示・収集は常に行われる。ラベルを動作に合わせる
        self.reasoning_checkbox = QCheckBox("推論を要求")
        self.reasoning_checkbox.setChecked(True)
        self.reasoning_checkbox.setToolTip(
            "オンのとき、思考レベルを指定して推論を要求します。\n"
            "オフにすると推論パラメータを送りません（モデルの既定で\n"
            "推論が有効になる場合があります）。"
        )
        layout.addWidget(self.reasoning_checkbox)

        layout.addWidget(QLabel("ランダム性:"))
        self.temperature_spin = QSpinBox()
        self.temperature_spin.setRange(0, 20)
        self.temperature_spin.setValue(7)
        self.temperature_spin.setSuffix(" (×0.1)")
        self.temperature_spin.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.temperature_spin)

        layout.addWidget(QLabel("最大トークン:"))
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(100, 1_000_000)
        self.max_tokens_spin.setValue(10_000)
        self.max_tokens_spin.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.max_tokens_spin)

        layout.addStretch()
        return layout

    def _build_button_panel(self) -> QHBoxLayout:
        layout = QHBoxLayout()

        self.send_button    = self._make_button("送信",           self._send_message)
        self.regen_button   = self._make_button("再生成",         self._regenerate)
        self.cancel_button  = self._make_button("キャンセル",     self._cancel_request)
        self.clear_button   = self._make_button("クリア",         self._clear_conversation)
        self.save_json_btn  = self._make_button("保存(JSON)",     self._save_conversation)
        self.export_md_btn  = self._make_button("保存(MD)",       self._export_markdown)
        self.load_button    = self._make_button("読み込み",       self._load_conversation)
        self.edit_button    = self._make_button("編集モード",     self._toggle_edit_mode,
                                                checkable=True)

        self.cancel_button.setEnabled(False)
        self.regen_button.setEnabled(False)
        self.regen_button.setToolTip("直前の応答を破棄して、同じ入力で応答し直します")

        for btn in (self.send_button, self.regen_button, self.cancel_button,
                    self.clear_button, self.save_json_btn, self.export_md_btn,
                    self.load_button, self.edit_button):
            layout.addWidget(btn)
        return layout

    # ── ウィジェット生成ヘルパー ──────────────────────────────

    def _make_button(self, text: str, slot, *, checkable: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setStyleSheet(BUTTON_STYLE)
        btn.setFont(QFont("MS UI Gothic", 9))
        btn.setCheckable(checkable)
        btn.clicked.connect(slot)
        return btn

    def _make_dialog(self, title: str, text: str,
                     buttons=QMessageBox.Ok) -> QMessageBox:
        """ダークテーマ対応の QMessageBox を生成するヘルパー。"""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(buttons)
        box.setStyleSheet(DIALOG_STYLE)
        return box

    # ══════════════════════════════════════════════════════════
    # 設定の永続化（QSettings）
    # ══════════════════════════════════════════════════════════

    def _load_settings(self):
        s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
        model = s.value("model", list(MODEL_CONFIGS.keys())[0])
        if model in MODEL_CONFIGS:
            self.model_combo.setCurrentText(model)
        level = s.value("thinking_level", "medium")
        if level in THINKING_LEVELS:
            self.thinking_level_combo.setCurrentText(level)
        self.temperature_spin.setValue(int(s.value("temperature", 7)))
        self.max_tokens_spin.setValue(int(s.value("max_tokens", 10_000)))
        self.reasoning_checkbox.setChecked(s.value("reasoning", True, type=bool))
        sp = s.value("system_prompt", "")
        if sp:
            self.system_prompt_input.setPlainText(sp)
            self._set_system_prompt_expanded(True)      # 中身があれば開いておく
        # 起動時の復元は「変更」ではない
        self._mark_dirty(False)

    def _save_settings(self):
        s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
        s.setValue("model",          self.model_combo.currentText())
        s.setValue("thinking_level", self.thinking_level_combo.currentText())
        s.setValue("temperature",    self.temperature_spin.value())
        s.setValue("max_tokens",     self.max_tokens_spin.value())
        s.setValue("reasoning",      self.reasoning_checkbox.isChecked())
        s.setValue("system_prompt",  self.system_prompt_input.toPlainText())

    # ══════════════════════════════════════════════════════════
    # モデル変更
    # ══════════════════════════════════════════════════════════

    def _on_model_changed(self, model_id: str):
        cfg = get_model_config(model_id)
        self.reasoning_checkbox.setEnabled(cfg["supports_reasoning"])
        self.reasoning_text.setVisible(cfg["supports_reasoning"])
        self.thinking_level_combo.setEnabled(
            cfg["supports_reasoning"] and cfg["supports_thinking_level"]
        )
        self._refresh_thinking_levels(cfg)

        # 出力上限をモデルに合わせる（超えると API エラーになる）
        max_out = cfg["max_completion_tokens"]
        if max_out:
            self.max_tokens_spin.setMaximum(int(max_out))
            self.max_tokens_spin.setToolTip(f"このモデルの出力上限: {int(max_out):,}")
        else:
            self.max_tokens_spin.setMaximum(1_000_000)
            self.max_tokens_spin.setToolTip("")

        self._update_image_support()
        self._update_usage_label()

        if cfg["supports_reasoning"]:
            extra = " (思考レベル設定あり)" if cfg["supports_thinking_level"] else ""
            self.statusBar().showMessage(
                f"{cfg['display_name']} — 推論機能が利用できます{extra}"
            )
        else:
            self.statusBar().showMessage(
                f"{cfg['display_name']} — 推論機能は利用できません"
            )

    def _refresh_thinking_levels(self, config: dict):
        """
        思考レベルの選択肢をモデルに合わせる。

        受け付ける effort はモデルごとに違う（DeepSeek に minimal は無く、
        Grok に xhigh は無い）。一律の選択肢を出すと表示と実挙動が食い違う。
        """
        levels = config.get("reasoning_efforts") or THINKING_LEVELS
        current = self.thinking_level_combo.currentText()
        if [self.thinking_level_combo.itemText(i)
                for i in range(self.thinking_level_combo.count())] == list(levels):
            return

        self.thinking_level_combo.blockSignals(True)
        self.thinking_level_combo.clear()
        self.thinking_level_combo.addItems(levels)
        # 直前の選択を保てないときはモデルの既定値へ落とす
        for candidate in (current, config.get("reasoning_default"), "medium"):
            if candidate and candidate in levels:
                self.thinking_level_combo.setCurrentText(candidate)
                break
        self.thinking_level_combo.blockSignals(False)

    def _update_image_support(self):
        """画像を受け付けないモデルでは添付ボタンを塞ぐ。"""
        allowed = supports_images(self.model_combo.currentText())
        self.add_image_btn.setEnabled(allowed)
        self.add_image_btn.setToolTip(
            "" if allowed else "このモデルは画像入力に対応していません"
        )
        if not allowed and self.selected_images:
            self.selected_images.clear()
            self._update_image_info()
            self.statusBar().showMessage(
                "このモデルは画像入力に対応していないため、添付を解除しました"
            )

    # ══════════════════════════════════════════════════════════
    # モデルカタログ
    # ══════════════════════════════════════════════════════════

    def _start_catalog_fetch(self):
        self._catalog_worker = ModelCatalogWorker(self)
        self._catalog_worker.loaded.connect(self._on_catalog_loaded)
        self._catalog_worker.failed.connect(self._on_catalog_failed)
        self._catalog_worker.start()

    def _on_catalog_loaded(self, catalog: dict, from_cache: bool):
        MODEL_CATALOG.clear()
        MODEL_CATALOG.update(catalog)

        # 先に UI へ反映する。逆順にすると _on_model_changed が
        # ステータスバーを上書きし、下の警告が見えなくなる
        self._on_model_changed(self.model_combo.currentText())

        missing = [m for m in MODEL_CONFIGS if m not in MODEL_CATALOG]
        source  = "キャッシュ" if from_cache else "取得"
        message = f"モデル情報を{source}しました（{len(MODEL_CATALOG)} 件）"
        if missing:
            # ID が変わった・提供終了したモデルは送信すると失敗する
            message += f" ｜ 未掲載: {', '.join(missing)}"
        self.statusBar().showMessage(message)

    def _on_catalog_failed(self, reason: str):
        self.statusBar().showMessage(
            f"モデル情報を取得できませんでした（手書き定義で動作します）: {reason}"
        )

    # ══════════════════════════════════════════════════════════
    # 画像管理
    # ══════════════════════════════════════════════════════════

    _EXT_MIME = {
        ".jpg":  "image/jpeg", ".jpeg": "image/jpeg",
        ".png":  "image/png",  ".gif":  "image/gif",
        ".bmp":  "image/bmp",
    }

    # 本文に取り込めるテキストファイル
    _TEXT_EXTS = {
        ".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".yaml", ".yml",
        ".py", ".js", ".ts", ".html", ".css", ".xml", ".ini", ".log", ".srt",
    }
    _MAX_TEXT_BYTES = 512 * 1024

    def _add_image(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "画像を選択", "",
            "Image Files (*.png *.jpg *.jpeg *.bmp *.gif);;All Files (*)"
        )
        for path in paths:
            self._attach_image_file(path)
        self._update_image_info()

    # 添付は履歴に残り、以降のターンで毎回再送される。
    # 上限が無いと、気づかないうちに毎ターンの入力コストが跳ね上がる
    MAX_IMAGE_BYTES = 4 * 1024 * 1024
    MAX_IMAGE_COUNT = 8

    # QImageReader が返す形式名 → MIME
    _FORMAT_MIME = {
        "png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg",
        "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
    }

    def _attach_image_file(self, path: str) -> bool:
        name = os.path.basename(path)
        if len(self.selected_images) >= self.MAX_IMAGE_COUNT:
            self._make_dialog(
                "添付できません",
                f"画像は同時に {self.MAX_IMAGE_COUNT} 枚までです。",
            ).exec_()
            return False
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            self._make_dialog("画像読み込みエラー", f"{name}\n{exc}").exec_()
            return False
        if size > self.MAX_IMAGE_BYTES:
            self._make_dialog(
                "サイズ超過",
                f"{name} は {size / 1024 / 1024:.1f}MB あります。\n"
                f"{self.MAX_IMAGE_BYTES // 1024 // 1024}MB までにしてください。\n"
                "（添付画像は以降のターンでも毎回送信されます）",
            ).exec_()
            return False

        # 拡張子ではなく中身で判定する。拡張子任せだと、画像でないファイルを
        # image/jpeg と偽って送ってしまう
        image_format = bytes(QImageReader(path).format()).decode("ascii", "ignore").lower()
        mime = self._FORMAT_MIME.get(image_format)
        if not mime:
            self._make_dialog(
                "画像として読み込めません",
                f"{name} は対応している画像形式ではありません。",
            ).exec_()
            return False

        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        except OSError as exc:
            self._make_dialog("画像読み込みエラー", f"{name}\n{exc}").exec_()
            return False

        self.selected_images.append((b64, mime, name))
        return True

    # ── 貼り付け・ドロップ ────────────────────────────────────

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasImage() or mime.hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        mime = event.mimeData()
        if mime.hasImage() or mime.hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if self._accept_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _accept_mime(self, mime) -> bool:
        """
        クリップボード／ドロップの中身を取り込む。
        画像は添付へ、テキストファイルは本文へ。処理したら True。
        """
        added_images = 0
        added_texts  = 0
        skipped: list[str] = []

        if mime.hasImage():
            if self._attach_clipboard_image(mime):
                added_images += 1
            else:
                skipped.append("クリップボードの画像")

        for url in (mime.urls() if mime.hasUrls() else []):
            if not url.isLocalFile():
                skipped.append(url.toString())
                continue
            path = url.toLocalFile()
            ext  = os.path.splitext(path)[1].lower()
            if ext in self._EXT_MIME:
                if not supports_images(self.model_combo.currentText()):
                    skipped.append(f"{os.path.basename(path)} (画像非対応モデル)")
                elif self._attach_image_file(path):
                    added_images += 1
            elif ext in self._TEXT_EXTS:
                if self._insert_text_file(path):
                    added_texts += 1
                else:
                    skipped.append(os.path.basename(path))
            else:
                skipped.append(f"{os.path.basename(path)} (非対応の形式)")

        if added_images:
            self._update_image_info()

        if not (added_images or added_texts or skipped):
            return False

        report = []
        if added_images:
            report.append(f"画像 {added_images} 件を添付")
        if added_texts:
            report.append(f"テキスト {added_texts} 件を本文に挿入")
        if skipped:
            report.append(f"取り込めず: {', '.join(skipped)}")
        self.statusBar().showMessage(" ｜ ".join(report))
        return bool(added_images or added_texts)

    def _attach_clipboard_image(self, mime) -> bool:
        if not supports_images(self.model_combo.currentText()):
            self.statusBar().showMessage("このモデルは画像入力に対応していません")
            return False
        if len(self.selected_images) >= self.MAX_IMAGE_COUNT:
            self.statusBar().showMessage(
                f"画像は同時に {self.MAX_IMAGE_COUNT} 枚までです"
            )
            return False
        image = QImage(mime.imageData())
        if image.isNull():
            return False
        # QBuffer は渡された QByteArray を所有しない。
        # QBuffer(QByteArray()) と書くと一時オブジェクトが即解放され、
        # 解放済みメモリへ書き込んでヒープを壊す。引数なしの内部バッファを使う。
        buffer = QBuffer()
        buffer.open(QBuffer.WriteOnly)
        if not image.save(buffer, "PNG"):
            return False
        buffer.close()

        # ファイル添付と同じ上限を貼り付け経路にも掛ける。
        # 画面全体のスクリーンショットなどは容易に数MBになる
        data = bytes(buffer.data())
        if len(data) > self.MAX_IMAGE_BYTES:
            self._make_dialog(
                "サイズ超過",
                f"貼り付けた画像は {len(data) / 1024 / 1024:.1f}MB あります。\n"
                f"{self.MAX_IMAGE_BYTES // 1024 // 1024}MB までにしてください。\n"
                "（添付画像は以降のターンでも毎回送信されます）",
            ).exec_()
            return False

        b64 = base64.b64encode(data).decode("utf-8")
        index = sum(1 for img in self.selected_images
                    if img[2].startswith("clipboard")) + 1
        self.selected_images.append((b64, "image/png", f"clipboard-{index}.png"))
        return True

    def _insert_text_file(self, path: str) -> bool:
        """テキストファイルの中身を、ファイル名付きで入力欄へ差し込む。"""
        try:
            size = os.path.getsize(path)
            if size > self._MAX_TEXT_BYTES:
                self._make_dialog(
                    "サイズ超過",
                    f"{os.path.basename(path)} は "
                    f"{size / 1024:.0f}KB あります。\n"
                    f"{self._MAX_TEXT_BYTES // 1024}KB までに収めてください。",
                ).exec_()
                return False
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                body = f.read()
        except OSError:
            return False

        name = os.path.basename(path)
        # 本文中の最長のバッククォート連より 1 本長くしないと閉じてしまう
        longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
        fence = "`" * max(3, longest + 1)
        block = f"\n{name}:\n{fence}\n{body.rstrip()}\n{fence}\n"

        cursor = self.message_input.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(block)
        self.message_input.setTextCursor(cursor)
        return True

    def _clear_images(self):
        self.selected_images.clear()
        self._update_image_info()

    def _update_image_info(self):
        if self.selected_images:
            names = ", ".join(img[2] for img in self.selected_images)
            # base64 は元データの約 4/3。毎ターン再送されるので目安を出す
            total = sum(len(img[0]) for img in self.selected_images) * 3 // 4
            self.image_info_label.setText(
                f"選択された画像: {names}"
                f"（{len(self.selected_images)}/{self.MAX_IMAGE_COUNT} 枚, "
                f"計 {total / 1024:.0f}KB・毎ターン送信）"
            )
        else:
            self.image_info_label.setText("選択された画像: なし")

    # ══════════════════════════════════════════════════════════
    # メッセージ送受信
    # ══════════════════════════════════════════════════════════

    def _build_api_messages(self) -> list[dict]:
        """システムプロンプトを先頭に追加した API 送信用リストを返す。"""
        messages: list[dict] = []
        sp = self.system_prompt_input.toPlainText().strip()
        if sp:
            messages.append({
                "role": "system",
                "content": [{"type": "text", "text": sp}],
            })
        messages.extend(m.to_api() for m in self.conversation_history)
        return messages

    def _is_busy(self) -> bool:
        """API 応答の受信中、または受信済みで確定待ちかどうか。"""
        return self._request_active or \
            (self.worker is not None and self.worker.isRunning())

    def _send_message(self):
        # 送信ボタンを無効化しても Ctrl+Enter は素通りするため、ここで止める。
        # 二重送信すると 2 つ目のワーカーが self.worker とストリーム位置を
        # 上書きして表示が混線する。
        if self._is_busy():
            self.statusBar().showMessage(
                "応答中です。完了を待つかキャンセルしてください"
            )
            return

        if self.is_editing:
            self._toggle_edit_mode()

        text = self.message_input.toPlainText().strip()
        if not text and not self.selected_images:
            return

        self.message_input.clear()

        content: list[dict] = []
        if text:
            content.append({"type": "text", "text": text})
        for b64, mime, _ in self.selected_images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })

        message = Message.user(content)
        self._pending_user_message = message     # 0チャンクで中断したら取り下げる
        self.conversation_history.append(message)
        self._append_message(message)
        self._clear_images()
        self._update_usage_label()
        self._mark_dirty()

        self._last_api_messages = self._build_api_messages()
        self._start_request(self._last_api_messages)

    def _start_request(self, messages: list[dict]):
        model_id = self.model_combo.currentText()
        cfg      = get_model_config(model_id)

        # 前回のスレッドが残ったまま self.worker を差し替えると、
        # 実行中の QThread が GC 対象になりプロセスごと落ちる
        self._detach_worker()

        self.worker = ApiWorker(
            api_key        = self.api_key,
            messages       = messages,
            use_reasoning  = self.reasoning_checkbox.isChecked(),
            temperature    = self.temperature_spin.value() / 10.0,
            max_tokens     = self.max_tokens_spin.value(),
            model          = model_id,
            thinking_level = self.thinking_level_combo.currentText(),
            model_config   = cfg,          # 実行中のカタログ差し替えに影響されない
        )
        # NOTE: これらのハンドラに @pyqtSlot を付けてはいけない。
        # 付けるとネイティブ Qt 経路になり、disconnect 済みでもキュー済みの
        # シグナルが後着しうる（通常の Python メソッドなら PyQt が内部プロキシを
        # 削除し、未配送イベントも一緒に消える。tests/test_qt_signal_semantics.py
        # で検証している）。付けるならリクエスト世代の判定を同時に入れること。
        self.worker.chunk_received.connect(self._on_chunk_received)
        self.worker.response_finished.connect(self._on_stream_finished)
        self.worker.error.connect(self._handle_api_error)
        # 後始末は生成時に繋ぐ。切り離し時に繋ぐと、その直前に終了した場合に
        # finished を取り逃がしてリストへ残り続ける
        worker = self.worker
        worker.finished.connect(lambda: self._discard_worker(worker))
        self._request_active = True
        self.worker.start()

        self._start_streaming_display(model_id)
        self.statusBar().showMessage(f"{cfg['display_name']} が応答中…")
        self.send_button.setEnabled(False)
        self.regen_button.setEnabled(False)
        self.cancel_button.setEnabled(True)

    def _cancel_request(self):
        """
        ワーカーを切り離し、その場で表示と履歴を確定する。

        ApiWorker.cancel() は即座には効かない（受信待ちでブロックしていると
        リードタイムアウトまで戻らない）。終了を待ってから画面を更新する作りにすると、
        応答が途切れた時ほど長く固まる。そこで通知を切ってスレッドを見捨て、
        UI だけ先に確定させる。
        """
        if not self._is_busy():
            return
        self._detach_worker()
        self._on_stream_finished("", {}, STATUS_CANCELLED)

    def _detach_worker(self):
        """
        現在のワーカーを切り離す。終了は待たない
        （受信待ちでブロックしていると戻ってこないため）。
        """
        worker, self.worker = self.worker, None
        if worker is None:
            return
        for signal in (worker.chunk_received, worker.response_finished, worker.error):
            try:
                signal.disconnect()
            except TypeError:
                pass                      # 未接続なら何もしなくてよい
        if worker.isRunning():
            self._retire_worker(worker)
            worker.cancel()

    def _retire_worker(self, worker: ApiWorker):
        """
        見捨てたスレッドを、実際に終了するまで保持する。
        参照を落とすと実行中の QThread が GC され、プロセスごと落ちる。

        後始末の接続は生成時に済ませてある（_start_request）。
        ここへ来る前に終了していると finished を取り逃がすため、念のため確認する。
        """
        self._retired_workers.append(worker)
        if not worker.isRunning():
            self._discard_worker(worker)

    def _discard_worker(self, worker: ApiWorker):
        if worker in self._retired_workers:
            self._retired_workers.remove(worker)

    # ── ストリーミング表示 ────────────────────────────────────

    def _start_streaming_display(self, model_id: str):
        """送信者プレフィックスを挿入し、本文受信開始位置を記録する。"""
        cfg = get_model_config(model_id)
        self._stream_buffer      = ""
        self._stream_pending     = ""
        self._stream_placeholder = False
        # 応答中にモデルを切り替えられても、この応答の出所は変わらない
        self._stream_model       = model_id

        cursor = self.conversation_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._stream_message_start = cursor.position()   # ← プレフィックス前
        cursor.insertHtml(
            f"<font color='{cfg['color']}'>"
            f"<b>{html.escape(cfg['display_name'])}:</b></font> "
        )
        cursor.setCharFormat(QTextCharFormat())          # 本文に見出しの書式を引き継がせない
        self._stream_content_start = cursor.position()   # ← 本文開始
        self.conversation_text.setTextCursor(cursor)

    def _on_chunk_received(self, chunk: str):
        self._stream_buffer  += chunk
        self._stream_pending += chunk
        if not self._stream_timer.isActive():
            self._stream_timer.start()

    def _flush_stream(self):
        """溜まったチャンクをまとめて描画する。"""
        if not self._stream_pending:
            self._stream_timer.stop()
            return

        text, self._stream_pending = self._stream_pending, ""

        # 元々最下部を見ていた時だけ追従する（読み返し中に引き戻さない）
        bar       = self.conversation_text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4

        # 表示中のカーソル（＝利用者の選択範囲）に触れない独立カーソルを使う
        cursor = QTextCursor(self.conversation_text.document())
        cursor.movePosition(QTextCursor.End)
        if self._stream_placeholder:
            cursor.deletePreviousChar()          # 前回の "▌" を除去
        # 確定前はプレーンテキストで積む（Markdown は完了時に一括変換）
        cursor.insertText(text)
        cursor.insertText("▌")                   # ストリーム中カーソル
        self._stream_placeholder = True

        if at_bottom:
            bar.setValue(bar.maximum())

    def _reset_stream_state(self):
        """描画タイマーと未描画バッファを止める。"""
        self._stream_timer.stop()
        self._stream_pending     = ""
        self._stream_placeholder = False

    def _on_stream_finished(self, reasoning: str, usage: dict,
                            status: str = STATUS_COMPLETED):
        self._request_active = False
        self._reset_stream_state()
        cancelled = status == STATUS_CANCELLED

        # 再生成を中断した場合は、部分的な新案より元の応答を残す
        if cancelled and self._regen_backup is not None:
            cursor = QTextCursor(self.conversation_text.document())
            cursor.setPosition(self._stream_message_start)
            cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            self._restore_regen_backup()
            self.send_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            return
        self._regen_backup = None

        bar       = self.conversation_text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4

        cursor = QTextCursor(self.conversation_text.document())
        if cancelled and not self._stream_buffer:
            # 一文字も届いていないので、送信者名ごと取り消す
            cursor.setPosition(self._stream_message_start)
            cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            # 未回答の user 発言だけが残ると、次の送信で user が連続する。
            # 送った内容を入力欄へ戻し、送信前の状態に近づける
            self._take_back_pending_message()
        else:
            # プレーンテキスト部分（"▌" 含む）を Markdown HTML で置き換え
            cursor.setPosition(self._stream_content_start)
            cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            cursor.insertHtml(render_markdown(self._stream_buffer))
            note = STATUS_NOTES.get(status)
            if note:
                cursor.insertHtml(
                    f" <i><font color='#888888'>{html.escape(note)}</font></i>"
                )
            cursor.insertHtml("<br><br>")

            # 履歴に保存。キャンセル時も画面に残す以上、履歴と食い違わせない。
            # 終わり方は status として残す（再描画・保存でも失われない）
            self.conversation_history.append(Message.assistant(
                self._stream_buffer,
                model     = self._stream_model,
                reasoning = reasoning,
                usage     = usage,
                status    = status,
            ))

        if at_bottom:
            bar.setValue(bar.maximum())

        # 推論テキスト（キャンセル時は取得できていないので前回表示を残す）
        cfg = get_model_config(self._stream_model or self.model_combo.currentText())
        if reasoning:
            self.reasoning_text.setPlainText(reasoning)
        elif not cancelled:
            self.reasoning_text.setPlainText(
                "推論プロセスは提供されていません" if cfg["supports_reasoning"]
                else "このモデルは推論機能をサポートしていません"
            )

        if status in STATUS_NOTES:
            self.statusBar().showMessage(
                {STATUS_CANCELLED: "キャンセルしました",
                 STATUS_TRUNCATED: "最大トークンに達したため、応答が途中で終了しました",
                 STATUS_FILTERED:  "応答がフィルタにより打ち切られました",
                 STATUS_ERROR:     "応答がエラーで終了しました"}[status]
            )
        else:
            # ステータスバーにトークン使用量とコストを表示
            p_tok = usage.get("prompt_tokens",     "?")
            c_tok = usage.get("completion_tokens", "?")
            parts = [f"完了 ｜ 入力: {p_tok} tok ／ 出力: {c_tok} tok"]
            cost = _to_float(usage.get("cost"))
            if cost is not None:
                parts.append(f"今回 {_format_cost(cost)}")
            total = self._session_cost()
            if total:
                parts.append(f"累計 {_format_cost(total)}")
            self.statusBar().showMessage(" ｜ ".join(parts))

        self._update_usage_label()
        self._mark_dirty()
        self.send_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _handle_api_error(self, error_message: str):
        # エラー発生時は部分ストリームを削除してエラーメッセージに差し替え
        self._request_active = False
        self._reset_stream_state()
        cursor = QTextCursor(self.conversation_text.document())
        cursor.setPosition(self._stream_message_start)
        cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()

        self._append_to_conversation(
            "システム", f"エラー: {error_message}", color=SYSTEM_COLOR
        )
        self.statusBar().showMessage(f"エラー: {error_message}")
        self.send_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        # 再生成が失敗したなら、退避した元の応答を戻す
        self._restore_regen_backup()
        self._update_usage_label()

        # 再試行ダイアログ
        box = self._make_dialog(
            "APIエラー",
            f"{error_message}\n\n再試行しますか？",
            QMessageBox.Retry | QMessageBox.Cancel,
        )
        box.button(QMessageBox.Retry).setText("再試行")
        box.button(QMessageBox.Cancel).setText("閉じる")
        if box.exec_() == QMessageBox.Retry:
            self._retry_last_request()

    def _retry_last_request(self):
        if self._last_api_messages:
            self._start_request(self._last_api_messages)

    # ══════════════════════════════════════════════════════════
    # 再生成
    # ══════════════════════════════════════════════════════════

    def _can_regenerate(self) -> bool:
        return (
            not self._is_busy()
            and bool(self.conversation_history)
            and self.conversation_history[-1].role == "assistant"
        )

    def _regenerate(self):
        """
        直前の応答を捨てて、同じ入力で応答し直す。

        旧応答は生成が成功するまで手元に残す。失敗・中断したときに
        元の応答まで失うと、書き直しの素材ごと消えてしまう。
        """
        if self._is_busy():
            self.statusBar().showMessage("応答中です")
            return
        if self.is_editing:
            self._toggle_edit_mode()
        if not self._can_regenerate():
            self.statusBar().showMessage("再生成できる応答がありません")
            return

        self._regen_backup = self.conversation_history.pop()
        self._redraw_conversation()
        self._show_last_reasoning()
        self._update_usage_label()
        self._mark_dirty()

        self._last_api_messages = self._build_api_messages()
        self._start_request(self._last_api_messages)

    def _take_back_pending_message(self):
        """
        一文字も応答が来ないうちに中断した場合、直前に送った user 発言を
        履歴から取り下げ、本文を入力欄へ戻す。
        """
        pending = self._pending_user_message
        self._pending_user_message = None
        if pending is None or not self.conversation_history:
            return
        if self.conversation_history[-1] is not pending:
            return          # 途中で履歴が変わっているので触らない

        self.conversation_history.pop()
        self._redraw_conversation()
        if pending.text:
            existing = self.message_input.toPlainText()
            self.message_input.setPlainText(
                f"{pending.text}\n{existing}" if existing else pending.text
            )
        self.statusBar().showMessage("送信を取り消し、入力内容を戻しました")

    def _restore_regen_backup(self) -> bool:
        """再生成に失敗した場合、退避しておいた旧応答を戻す。"""
        if self._regen_backup is None:
            return False
        self.conversation_history.append(self._regen_backup)
        self._regen_backup = None
        self._redraw_conversation()
        self._show_last_reasoning()
        self._update_usage_label()
        self.statusBar().showMessage("再生成を取り消し、元の応答に戻しました")
        return True

    # ══════════════════════════════════════════════════════════
    # 使用量・コスト表示
    # ══════════════════════════════════════════════════════════

    def _session_cost(self) -> float:
        return sum(
            _to_float(m.usage.get("cost")) or 0.0
            for m in self.conversation_history
        )

    def _last_context_tokens(self) -> int | None:
        """
        次回リクエストの入力トークン数の目安。
        直近応答の実測値（入力＋出力）を使う。推測より確実。
        """
        for message in reversed(self.conversation_history):
            if message.role != "assistant" or not message.usage:
                continue
            prompt     = message.usage.get("prompt_tokens")
            completion = message.usage.get("completion_tokens")
            if isinstance(prompt, int):
                return prompt + (completion if isinstance(completion, int) else 0)
        return None

    def _update_usage_label(self):
        """履歴の規模・次回入力の概算コスト・セッション累計を表示する。"""
        cfg    = get_model_config(self.model_combo.currentText())
        pieces: list[str] = []

        tokens = self._last_context_tokens()
        if tokens is None:
            pieces.append("履歴: 未計測")
        else:
            piece = f"履歴: 約 {tokens:,} tok"
            limit = cfg["context_length"]
            if limit:
                piece += f" / {int(limit):,} ({tokens / int(limit):.1%})"
            pieces.append(piece)

            # 入力量に応じて単価が上がるモデルがあるため、閾値を考慮する
            price = prompt_price(cfg, tokens)
            if price:
                # 会話が伸びるほど、毎ターンこの額が入力分として発生する
                pieces.append(f"次回入力: 約 {_format_cost(tokens * price)}")

        total = self._session_cost()
        if total:
            pieces.append(f"セッション累計: {_format_cost(total)}")

        self.usage_label.setText(" ｜ ".join(pieces))
        self.regen_button.setEnabled(self._can_regenerate())

    # ══════════════════════════════════════════════════════════
    # 会話表示
    # ══════════════════════════════════════════════════════════

    def _append_message(self, message: Message, scroll: bool = True):
        """Message を、その出所に応じた名前と色で表示する。"""
        self._append_to_conversation(
            message.display_name, message.content,
            is_user=(message.role == "user"),
            scroll=scroll, color=message.color,
            note=STATUS_NOTES.get(message.status),
        )

    def _append_to_conversation(
        self, sender: str, content,
        is_user: bool = False, scroll: bool = True,
        color: str = UNKNOWN_ASSISTANT_COLOR,
        note: str | None = None,
    ):
        """
        会話表示エリアにメッセージを追加する。
        is_user=True のときは Markdown レンダリングをスキップ。
        """
        cursor = self.conversation_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(
            f"<font color='{color}'><b>{html.escape(sender)}:</b></font> "
        )

        for part in normalize_content(content):
            ptype = part.get("type")
            if ptype == "text":
                text = part["text"]
                cursor.insertHtml(
                    html.escape(text).replace("\n", "<br>")
                    if is_user
                    else render_markdown(text)
                )
            elif ptype == "image_url":
                # 読み込んだ会話ファイル由来の値なので、検証もエスケープもせずに
                # 属性へ埋め込むと、属性を閉じて外部画像を差し込まれる
                url = (part.get("image_url") or {}).get("url", "")
                if is_safe_image_url(url):
                    cursor.insertHtml(
                        f'<img src="{html.escape(url, quote=True)}" width="200" '
                        f'style="max-width:200px;max-height:200px;margin:5px;">'
                    )
                elif url:
                    cursor.insertHtml(
                        "<i><font color='#e88080'>"
                        "（対応していない形式の画像のため表示できません）</font></i>"
                    )

        if note:
            cursor.insertHtml(
                f" <i><font color='#888888'>{html.escape(note)}</font></i>"
            )

        if not self.is_editing:
            cursor.insertHtml("<br><br>")

        if scroll:
            self.conversation_text.ensureCursorVisible()

    def _redraw_conversation(self):
        """履歴から会話全体を再描画する（読み込み・編集終了時に使用）。"""
        self.conversation_text.clear()
        for message in self.conversation_history:
            self._append_message(message, scroll=False)
        self.conversation_text.ensureCursorVisible()

    def _show_last_reasoning(self):
        """履歴に残っている最後の推論を推論欄へ復元する。"""
        for message in reversed(self.conversation_history):
            if message.reasoning:
                self.reasoning_text.setPlainText(message.reasoning)
                return
        self.reasoning_text.clear()

    # ══════════════════════════════════════════════════════════
    # 検索（Ctrl+F）
    # ══════════════════════════════════════════════════════════

    def _open_search(self):
        if self._search_dialog is None or not self._search_dialog.isVisible():
            self._search_dialog = SearchDialog(self.conversation_text, self)
        self._search_dialog.show()
        self._search_dialog.raise_()
        self._search_dialog.search_input.setFocus()

    # ══════════════════════════════════════════════════════════
    # 編集モード
    # ══════════════════════════════════════════════════════════

    # 区切りは自然文に現れない形にする。
    # 旧実装は行頭の「あなた:」等で切っていたため、本文に二人称の呼びかけや
    # 台本形式が含まれると、そこでメッセージが分割・改役されていた。
    # 末尾には表示名（モデル名）が続くので、role の後ろは緩く受ける
    _EDIT_DELIMITER_RE = re.compile(
        r"^─{3,}\s*メッセージ\s*(?P<index>\d+)\s*"
        r"\[(?P<role>user|assistant|system)\].*$"
    )

    @staticmethod
    def _edit_delimiter(index: int, message: Message) -> str:
        return (f"──── メッセージ {index} [{message.role}] "
                f"{message.display_name} ────")

    def _toggle_edit_mode(self):
        # 応答中に編集へ入ると、文書を作り替えてストリーム位置が壊れる。
        # 終了側は塞がない（入れない以上、応答中に編集中になることはない）
        if not self.is_editing and self._is_busy():
            self.edit_button.setChecked(False)
            self.statusBar().showMessage("応答中は編集モードに入れません")
            return

        self.is_editing = not self.is_editing
        self.conversation_text.setReadOnly(not self.is_editing)

        # コードから呼ばれた場合、ボタンの押下状態が実際とずれる
        self.edit_button.setChecked(self.is_editing)

        if self.is_editing:
            self.edit_button.setText("編集終了")
            self.statusBar().showMessage(
                "編集モード: 区切り行はそのまま残してください（画像情報は失われます）"
            )
            self.conversation_text.setPlainText(self._serialize_for_editing())
        else:
            self.edit_button.setText("編集モード")
            self.statusBar().showMessage("編集モードを終了しました")
            self._sync_history_from_editor()

    def _serialize_for_editing(self) -> str:
        """
        履歴を編集用のプレーンテキストにする。
        本文は一切加工しない（字下げの全角スペースや末尾の空行も保つ）。
        """
        lines: list[str] = []
        for index, message in enumerate(self.conversation_history, 1):
            if lines:
                lines.append("")        # メッセージ間の区切り（ちょうど1行）
            lines.append(self._edit_delimiter(index, message))
            lines.extend(message.text.split("\n"))
        return "\n".join(lines)

    def _parse_edited_text(self, document: str) -> list[Message] | None:
        """
        編集テキストを解析する。区切り行が1つも無ければ None を返す
        （全消しと区別できないため、履歴を破棄しないで呼び出し側に委ねる）。
        """
        blocks: list[tuple[int, str, list[str]]] = []
        for line in document.split("\n"):
            matched = self._EDIT_DELIMITER_RE.match(line)
            if matched:
                blocks.append((int(matched.group("index")),
                               matched.group("role"), []))
            elif blocks:
                blocks[-1][2].append(line)      # 本文はそのまま積む

        if not blocks:
            return None

        parsed: list[Message] = []
        for position, (index, role, body) in enumerate(blocks):
            # 直列化のときに足した区切りの空行を1つだけ戻す（最後尾には無い）
            if position < len(blocks) - 1 and body and body[-1] == "":
                body.pop()
            message = Message(role, [{"type": "text", "text": "\n".join(body)}])
            self._restore_metadata(message, index, role)
            parsed.append(message)
        return parsed

    def _restore_metadata(self, message: Message, index: int, role: str):
        """
        区切り行の番号を手がかりに、元のメッセージからメタ情報を戻す。

        本文を書き換えた場合、その推論内容やトークン使用量はもはやその本文の
        ものではない。model だけ残して破棄し、edited を立てる。
        """
        if not 1 <= index <= len(self.conversation_history):
            return
        old = self.conversation_history[index - 1]
        if old.role != role:
            return
        message.model     = old.model
        message.timestamp = old.timestamp
        if message.text == old.text:
            message.reasoning = old.reasoning
            message.usage     = old.usage
            message.status    = old.status
            message.edited    = old.edited
        else:
            message.edited = True

    def _sync_history_from_editor(self):
        """編集テキストを conversation_history に反映する。"""
        document = self.conversation_text.toPlainText()
        parsed   = self._parse_edited_text(document)

        if parsed is None and self.conversation_history:
            # 区切り行が消えている（全消しを含む）。取り込むと会話を丸ごと失う。
            # 会話を消したい場合は「クリア」を使ってもらう（確認が入る）
            self._make_dialog(
                "編集を取り込めません",
                "区切り行（──── メッセージ N [role] ────）が見つかりません。\n"
                "区切り行は消さずに編集してください。\n\n"
                "編集内容は破棄し、元の会話に戻します。\n"
                "会話をすべて消す場合は「クリア」を使ってください。",
            ).exec_()
            self._redraw_conversation()
            return

        self.conversation_history = parsed or []
        self._redraw_conversation()
        self._update_usage_label()
        self._mark_dirty()

    # ══════════════════════════════════════════════════════════
    # 保存・読み込み
    # ══════════════════════════════════════════════════════════

    def _confirm_discard(self, action: str) -> bool:
        """未保存の会話を捨てる操作の前に確認する。"""
        if not (self.conversation_history and self._dirty):
            return True
        box = self._make_dialog(
            "確認",
            f"未保存の変更があります。保存せずに{action}しますか？",
            QMessageBox.Yes | QMessageBox.No,
        )
        box.button(QMessageBox.Yes).setText(f"{action}する")
        box.button(QMessageBox.No).setText("やめる")
        box.setDefaultButton(QMessageBox.No)
        return box.exec_() == QMessageBox.Yes

    def _clear_conversation(self):
        # 応答中に文書を消すと、ストリームの書き戻し位置が壊れる
        if self._is_busy():
            self.statusBar().showMessage("応答中はクリアできません")
            return
        if not self._confirm_discard("クリア"):
            return
        if self.is_editing:
            self._toggle_edit_mode()
        self.conversation_history.clear()
        self.conversation_text.clear()
        self.reasoning_text.clear()
        self._update_usage_label()
        # 上書き先を引き継ぐと、Ctrl+S で前の会話を空で潰してしまう
        self._current_path = None
        self._mark_dirty(False)
        self.statusBar().showMessage("会話をクリアしました")

    def _mark_dirty(self, dirty: bool = True):
        self._dirty = dirty
        self._update_window_title()

    def _update_window_title(self):
        name = os.path.basename(self._current_path) if self._current_path else "未保存"
        mark = "*" if self._dirty else ""
        self.setWindowTitle(f"OpenRouter Chat - PyQt5 ｜ {mark}{name}")

    def _write_conversation(self, path: str) -> bool:
        """
        指定パスへ書き出す。成功したら True。

        直接上書きすると、書き込み途中で失敗したときに元の会話も失う。
        一時ファイルへ書いてから置き換える。
        """
        payload = {
            "version":        CONVERSATION_FORMAT_VERSION,
            "session_start":  self.session_start.isoformat(),
            "saved_at":       datetime.now().isoformat(),
            "model":          self.model_combo.currentText(),
            "thinking_level": self.thinking_level_combo.currentText(),
            "system_prompt":  self.system_prompt_input.toPlainText(),
            "conversation":   [m.to_json() for m in self.conversation_history],
        }
        temp_path = f"{path}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
        except Exception as exc:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            self._make_dialog("保存エラー", f"保存中にエラーが発生しました:\n{exc}").exec_()
            return False

        self._current_path = path
        self._mark_dirty(False)
        self.statusBar().showMessage(f"保存しました: {path}")
        return True

    def _quick_save(self) -> bool:
        """
        Ctrl+S。保存先が決まっていれば確認なしで上書きする。
        まだ決まっていない初回だけ、保存先を尋ねる。
        """
        if self.is_editing:
            self._toggle_edit_mode()
        if not self.conversation_history:
            self.statusBar().showMessage("保存する会話がありません")
            return False
        if not self._current_path:
            return self._save_conversation()
        return self._write_conversation(self._current_path)

    def _save_conversation(self) -> bool:
        """保存先を尋ねて書き出す。ダイアログを閉じた・失敗した場合は False。"""
        if self.is_editing:
            self._toggle_edit_mode()
        default = self._current_path or \
            f"conversation_{self.session_start.strftime('%Y%m%d_%H%M%S')}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "会話を保存 (JSON)", default,
            "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return False
        return self._write_conversation(path)

    # 別のビューアで開いたときに外部取得が起こりうる記法
    _REMOTE_IMAGE_RE = re.compile(
        r"!\[[^\]]*\]\(\s*https?://|<img[^>]+src\s*=\s*[\"']?\s*https?://",
        re.IGNORECASE,
    )

    def _export_markdown(self):
        if self.is_editing:
            self._toggle_edit_mode()

        # 本文はモデル出力のまま書き出す。アプリ内では描画時に遮断しているが、
        # 別の Markdown ビューアは外部画像を取得しうる
        if any(self._REMOTE_IMAGE_RE.search(m.text) for m in self.conversation_history):
            box = self._make_dialog(
                "確認",
                "会話に外部URLの画像記法が含まれています。\n"
                "書き出した .md を別のビューアで開くと、その画像を\n"
                "取得しに行く可能性があります。\n\n"
                "このまま書き出しますか？",
                QMessageBox.Yes | QMessageBox.No,
            )
            box.button(QMessageBox.Yes).setText("書き出す")
            box.button(QMessageBox.No).setText("やめる")
            box.setDefaultButton(QMessageBox.No)
            if box.exec_() != QMessageBox.Yes:
                return

        ts   = self.session_start.strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "Markdown としてエクスポート", f"conversation_{ts}.md",
            "Markdown Files (*.md);;All Files (*)"
        )
        if not path:
            return
        try:
            used_models = sorted({
                m.model for m in self.conversation_history
                if m.role == "assistant" and m.model
            }) or [self.model_combo.currentText()]
            header = (
                f"# 会話ログ\n\n"
                f"- 日時: {self.session_start.strftime('%Y-%m-%d %H:%M')}\n"
                f"- モデル: {', '.join(used_models)}\n\n---\n\n"
            )
            blocks = [
                f"## {msg.display_name}\n\n{msg.text}"
                for msg in self.conversation_history
            ]
            with open(path, "w", encoding="utf-8") as f:
                f.write(header + "\n\n".join(blocks) + "\n")
            self.statusBar().showMessage(f"MDエクスポート完了: {path}")
        except Exception as exc:
            self._make_dialog(
                "エクスポートエラー", f"エクスポート中にエラーが発生しました:\n{exc}"
            ).exec_()

    def _load_conversation(self):
        if self._is_busy():
            self.statusBar().showMessage("応答中は読み込みできません")
            return
        if not self._confirm_discard("読み込み"):
            return
        if self.is_editing:
            self._toggle_edit_mode()
        path, _ = QFileDialog.getOpenFileName(
            self, "会話を読み込み", "",
            "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 旧フォーマット（version なし、content が str）も from_json で吸収
            history = [Message.from_json(m) for m in data.get("conversation", [])]

            saved_model = data.get("model", "")
            if saved_model in MODEL_CONFIGS:
                self.model_combo.setCurrentText(saved_model)

            # 旧形式にはメッセージ毎のモデルが無い。
            # ファイル全体のモデルで補い、以後の再描画で名前がぶれないようにする。
            if data.get("version", 1) < 2 and saved_model:
                for message in history:
                    if message.role == "assistant" and not message.model:
                        message.model = saved_model

            self.conversation_history = history

            saved_level = data.get("thinking_level", "")
            if saved_level and self.thinking_level_combo.findText(saved_level) >= 0:
                self.thinking_level_combo.setCurrentText(saved_level)

            # 保存時のシステムプロンプトを復元する。
            # 復元しないと、別の会話を読み込んでも現在の設定のまま送ってしまう
            if "system_prompt" in data:
                self.system_prompt_input.setPlainText(data.get("system_prompt") or "")

            # 保存済みの開始時刻を引き継ぐ（MD出力や既定ファイル名の日付がずれる）
            try:
                self.session_start = datetime.fromisoformat(data["session_start"])
            except (KeyError, TypeError, ValueError):
                pass

            self._redraw_conversation()
            self._show_last_reasoning()
            self._update_usage_label()
            # 以降の Ctrl+S はこのファイルへ上書きする
            self._current_path = path
            self._mark_dirty(False)
            self.statusBar().showMessage(f"読み込み完了: {path}")

        except Exception as exc:
            self._make_dialog(
                "読み込みエラー", f"読み込み中にエラーが発生しました:\n{exc}"
            ).exec_()

    # ══════════════════════════════════════════════════════════
    # ウィンドウクローズ
    # ══════════════════════════════════════════════════════════

    def closeEvent(self, event):
        if self.is_editing:
            self._toggle_edit_mode()

        self._save_settings()  # 設定は必ず保存

        # 未保存の変更が無ければ問い合わせない
        if self.conversation_history and self._dirty:
            target = os.path.basename(self._current_path) if self._current_path \
                else "新しいファイル"
            box = self._make_dialog(
                "確認", f"未保存の変更があります。保存しますか？\n（保存先: {target}）",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            box.button(QMessageBox.Yes).setText("はい")
            box.button(QMessageBox.No).setText("いいえ")
            box.button(QMessageBox.Cancel).setText("キャンセル")
            box.setDefaultButton(QMessageBox.Yes)

            result = box.exec_()
            if result == QMessageBox.Cancel:
                event.ignore()
                return
            # 保存を選んだのにファイルダイアログを閉じた／失敗した場合は、
            # 保存したつもりで会話が消えないよう終了しない
            if result == QMessageBox.Yes and not self._quick_save():
                event.ignore()
                return

        # 終了が確定してから通信スレッドを止める。
        # 受信待ちでブロックしていると止まりきらないことがあるため、
        # 全体で 2 秒までに区切る（ワーカー毎に待つと件数分だけ待たされる）。
        workers = [w for w in [self.worker, *self._retired_workers,
                               self._catalog_worker] if w is not None]
        for worker in workers:
            if worker.isRunning() and isinstance(worker, ApiWorker):
                worker.cancel()

        deadline = time.monotonic() + 2.0
        for worker in workers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.wait(int(remaining * 1000))

        # 止まりきらなかったスレッドが残ると、通常終了では QThread の破棄で
        # 異常終了扱いになる。main() 側で終了方法を切り替えるために記録する
        self.threads_pending = any(w.isRunning() for w in workers)
        event.accept()


# ═══════════════════════════════════════════════════════════════
# エントリーポイント
# ═══════════════════════════════════════════════════════════════

def install_excepthook():
    """
    スロット内の未捕捉例外で PyQt5 がプロセスを即座に落とすのを防ぐ。
    握り潰さず、内容を提示した上で動作を継続させる。
    """
    in_hook = False

    def _hook(exc_type, exc_value, exc_tb):
        nonlocal in_hook
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stderr.write(detail)
        if in_hook:          # ダイアログ表示中の再入で無限ループにしない
            return
        # ウィジェットは GUI スレッド以外から作ってはいけない。
        # ワーカースレッドの例外でここに来た場合は記録だけにとどめる
        app = QApplication.instance()
        if app is None or QThread.currentThread() is not app.thread():
            return
        in_hook = True
        try:
            box = QMessageBox()
            box.setWindowTitle("予期しないエラー")
            box.setIcon(QMessageBox.Warning)
            box.setText(
                "内部エラーが発生しました。\n"
                "動作を継続しますが、直前の操作は完了していない可能性があります。"
            )
            box.setDetailedText(detail)
            box.setStyleSheet(DIALOG_STYLE)
            box.exec_()
        finally:
            in_hook = False

    sys.excepthook = _hook


def finish_application(window: "OpenRouterChatApp", exit_code: int):
    """
    終了処理。止まりきらないスレッドが残った場合だけ強制終了へ切り替える。

    通常終了だと実行中の QThread が破棄されて Qt が異常終了扱いにするため。
    設定・会話の保存は closeEvent で済んでいる。
    """
    if not window.threads_pending:
        sys.exit(exit_code)         # 通常はこちら。atexit も走る

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(FORCED_EXIT_CODE)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("OpenRouter Chat")
    app.setOrganizationName("OpenRouterChat")
    install_excepthook()

    # ダークパレット
    p = QPalette()
    p.setColor(QPalette.Window,          QColor(45, 45, 45))
    p.setColor(QPalette.WindowText,      Qt.white)
    p.setColor(QPalette.Base,            QColor(30, 30, 30))
    p.setColor(QPalette.AlternateBase,   QColor(45, 45, 45))
    p.setColor(QPalette.ToolTipBase,     Qt.white)
    p.setColor(QPalette.ToolTipText,     Qt.white)
    p.setColor(QPalette.Text,            Qt.black)
    p.setColor(QPalette.Button,          QColor(53, 53, 53))
    p.setColor(QPalette.ButtonText,      Qt.white)
    p.setColor(QPalette.BrightText,      Qt.red)
    p.setColor(QPalette.Link,            QColor(42, 130, 218))
    p.setColor(QPalette.Highlight,       QColor(42, 130, 218))
    p.setColor(QPalette.HighlightedText, Qt.black)
    app.setPalette(p)

    window = OpenRouterChatApp()
    window.show()
    finish_application(window, app.exec_())


if __name__ == "__main__":
    main()