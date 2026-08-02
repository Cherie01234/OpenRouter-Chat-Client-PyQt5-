"""
OpenRouter Chat v2 - 全改善案適用版
依存: pip install PyQt5 requests markdown
"""
import sys
import os
import json
import base64
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
    QTextCharFormat, QImage,
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
# モデル設定（ここだけ編集すれば新モデル追加可能）
# ═══════════════════════════════════════════════════════════════

MODEL_CONFIGS: dict[str, dict] = {
    "deepseek/deepseek-v4-pro": {
        "display_name": "DeepSeek",
        "color": "#7ec8a0",          # 会話表示の送信者色
        "supports_reasoning": True,
        "reasoning_type": "deepseek",
        "supports_thinking_level": False,
    },
    "deepseek/deepseek-v4-flash": {
        "display_name": "DeepSeek Flash",
        "color": "#5aa87f",          # Pro より少し濃い緑で区別
        "supports_reasoning": True,
        "reasoning_type": "deepseek",
        "supports_thinking_level": False,
    },
    "x-ai/grok-4.3": {
        "display_name": "Grok",
        "color": "#f5a623",
        "supports_reasoning": True,
        "reasoning_type": "grok",
        "supports_thinking_level": False,
    },
    "google/gemini-3-flash-preview": {
        "display_name": "Gemini",
        "color": "#c084fc",
        "supports_reasoning": True,
        "reasoning_type": "gemini",
        "supports_thinking_level": True,
    },
}

# OpenRouter の reasoning.effort が受け付ける値（弱い順）。
# 旧実装は Gemini に存在しない "level" フィールドを送っていた。
# effort と max_tokens は排他なので、effort だけを使う。
THINKING_LEVELS: list[str] = ["minimal", "low", "medium", "high", "xhigh"]

# ═══════════════════════════════════════════════════════════════
# モデルカタログ（OpenRouter /api/v1/models から取得）
# ═══════════════════════════════════════════════════════════════

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
        params  = set(entry.get("supported_parameters") or [])
        arch    = entry.get("architecture") or {}
        top     = entry.get("top_provider") or {}
        pricing = entry.get("pricing") or {}
        catalog[model_id] = {
            "supports_reasoning":      bool(params & {"reasoning", "include_reasoning"}),
            "supports_thinking_level": "reasoning_effort" in params,
            "context_length":          entry.get("context_length"),
            "max_completion_tokens":   top.get("max_completion_tokens"),
            "input_modalities":        list(arch.get("input_modalities") or []),
            "price_prompt":            _to_float(pricing.get("prompt")),
            "price_completion":        _to_float(pricing.get("completion")),
        }
    return catalog


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
    "reasoning_type":          None,
    "supports_thinking_level": False,
    "context_length":          None,
    "max_completion_tokens":   None,
    "input_modalities":        ["text"],
    "price_prompt":            None,
    "price_completion":        None,
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

USER_COLOR   = "#82b8e8"
SYSTEM_COLOR = "#e88080"
UNKNOWN_ASSISTANT_COLOR = "#e8e8e8"

# 保存フォーマットの版。1 = model/reasoning を持たない旧形式
CONVERSATION_FORMAT_VERSION = 2


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

    # ── 生成 ──────────────────────────────────────────────────

    @classmethod
    def user(cls, content) -> "Message":
        return cls("user", normalize_content(content), timestamp=_now())

    @classmethod
    def assistant(cls, text: str, model: str | None = None,
                  reasoning: str = "", usage: dict | None = None) -> "Message":
        return cls("assistant", [{"type": "text", "text": text}],
                   model=model, reasoning=reasoning,
                   usage=usage or {}, timestamp=_now())

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
        )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class _HtmlSanitizer(HTMLParser):
    """
    モデル出力由来の HTML から、外部リソースを取得しうる要素・属性を取り除く。

    QTextEdit は <img src="https://..."> を実際に取得しに行くため、素通しにすると
    「応答内容をモデルに指定された URL へ通知させる」経路になりうる。
    許可タグ以外はタグのみ剥がして中身のテキストは残す（unwrap）。
    """

    _VOID_TAGS = {"br", "hr", "img"}

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
        self._skip_depth = 0

    def result(self) -> str:
        return "".join(self._out)

    @staticmethod
    def _is_safe_url(attr: str, value: str) -> bool:
        url = value.strip().lower()
        if attr == "src":
            # 画像は埋め込み data: のみ許可（http(s)/file は外部・ローカル取得になる）
            return url.startswith("data:image/")
        return url.startswith(("http://", "https://", "mailto:"))

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
            self._skip_depth += 1
            return
        if self._skip_depth or tag not in self._ALLOWED_TAGS:
            return
        cleaned = self._clean_attrs(tag, attrs)
        if tag == "img" and "src=" not in cleaned:
            return          # src を落とした img は描画できないので要素ごと捨てる
        slash = "/" if tag in self._VOID_TAGS else ""
        self._out.append(f"<{tag}{cleaned}{slash}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self._DROP_CONTENT_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth or tag not in self._ALLOWED_TAGS or tag in self._VOID_TAGS:
            return
        self._out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._skip_depth:
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
    """

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
    chunk_received    = pyqtSignal(str)              # ストリーミング差分テキスト
    response_finished = pyqtSignal(str, dict, bool)  # (reasoning, usage, cancelled)
    error             = pyqtSignal(str)

    URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key: str, messages: list, use_reasoning: bool,
                 temperature: float, max_tokens: int, model: str,
                 thinking_level: str = "medium"):
        super().__init__()
        self.api_key       = api_key
        self.messages      = messages
        self.use_reasoning = use_reasoning
        self.temperature   = temperature
        self.max_tokens    = max_tokens
        self.model         = model
        self.thinking_level = thinking_level

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
        cfg = get_model_config(self.model)
        if not self.use_reasoning or not cfg["supports_reasoning"]:
            return {}
        if cfg["supports_thinking_level"] and self.thinking_level in THINKING_LEVELS:
            return {"reasoning": {"effort": self.thinking_level}}
        return {"reasoning": {"enabled": True}}

    # ── ストリーミング実行 ────────────────────────────────────

    def run(self):
        if self.isInterruptionRequested():
            return

        response       = None
        full_reasoning = ""
        usage: dict    = {}

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
                    data  = json.loads(line[6:])
                    delta = data["choices"][0].get("delta", {})

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

                    # 使用量（最終チャンクに含まれることが多い）
                    if data.get("usage"):
                        usage = data["usage"]

                except (json.JSONDecodeError, KeyError, IndexError):
                    pass

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
            cfg = get_model_config(self.model)
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

        self.response_finished.emit(
            full_reasoning, usage, self.isInterruptionRequested()
        )


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

        # 保存状態（Ctrl+S の上書き先と、未保存変更の有無）
        self._current_path: str | None = None
        self._dirty = False

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
        layout = QVBoxLayout(frame)
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
        layout.addWidget(self.system_prompt_input)

        return frame

    def _toggle_system_prompt(self):
        visible = self.system_prompt_input.isVisible()
        self.system_prompt_input.setVisible(not visible)
        self.sp_preset_row.setVisible(not visible)
        self.sp_toggle.setText(
            "▼ システムプロンプト" if not visible else "▶ システムプロンプト"
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

        self.reasoning_checkbox = QCheckBox("推論プロセスを表示")
        self.reasoning_checkbox.setChecked(True)
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
            # SP欄を自動展開
            self.system_prompt_input.setVisible(True)
            self.sp_toggle.setText("▼ システムプロンプト")

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

    def _attach_image_file(self, path: str) -> bool:
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            ext  = os.path.splitext(path)[1].lower()
            mime = self._EXT_MIME.get(ext, "image/jpeg")
            self.selected_images.append((b64, mime, os.path.basename(path)))
            return True
        except Exception as exc:
            self._make_dialog(
                "画像読み込みエラー", f"{path}\n読み込みに失敗しました: {exc}"
            ).exec_()
            return False

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
        b64 = base64.b64encode(bytes(buffer.data())).decode("utf-8")
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

        name  = os.path.basename(path)
        fence = "````" if "```" in body else "```"
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
            self.image_info_label.setText(f"選択された画像: {names}")
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
        )
        self.worker.chunk_received.connect(self._on_chunk_received)
        self.worker.response_finished.connect(self._on_stream_finished)
        self.worker.error.connect(self._handle_api_error)
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
        self._on_stream_finished("", {}, cancelled=True)

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
        """
        self._retired_workers.append(worker)
        worker.finished.connect(lambda: self._discard_worker(worker))

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
                            cancelled: bool = False):
        self._request_active = False
        self._reset_stream_state()

        bar       = self.conversation_text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4

        cursor = QTextCursor(self.conversation_text.document())
        if cancelled and not self._stream_buffer:
            # 一文字も届いていないので、送信者名ごと取り消す
            cursor.setPosition(self._stream_message_start)
            cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
        else:
            # プレーンテキスト部分（"▌" 含む）を Markdown HTML で置き換え
            cursor.setPosition(self._stream_content_start)
            cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            cursor.insertHtml(render_markdown(self._stream_buffer))
            if cancelled:
                cursor.insertHtml(
                    " <i><font color='#888888'>（キャンセルされました）</font></i>"
                )
            cursor.insertHtml("<br><br>")

            # 履歴に保存。キャンセル時も画面に残す以上、履歴と食い違わせない。
            self.conversation_history.append(Message.assistant(
                self._stream_buffer,
                model     = self._stream_model,
                reasoning = reasoning,
                usage     = usage,
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

        if cancelled:
            self.statusBar().showMessage("キャンセルしました")
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
        """直前の応答を捨てて、同じ入力で応答し直す。"""
        if self._is_busy():
            self.statusBar().showMessage("応答中です")
            return
        if self.is_editing:
            self._toggle_edit_mode()
        if not self._can_regenerate():
            self.statusBar().showMessage("再生成できる応答がありません")
            return

        self.conversation_history.pop()
        self._redraw_conversation()
        self._show_last_reasoning()
        self._update_usage_label()
        self._mark_dirty()

        self._last_api_messages = self._build_api_messages()
        self._start_request(self._last_api_messages)

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

            price = cfg["price_prompt"]
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
        )

    def _append_to_conversation(
        self, sender: str, content,
        is_user: bool = False, scroll: bool = True,
        color: str = UNKNOWN_ASSISTANT_COLOR,
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
                url = part.get("image_url", {}).get("url", "")
                if url.startswith("data:"):
                    cursor.insertHtml(
                        f'<img src="{url}" width="200" '
                        f'style="max-width:200px;max-height:200px;margin:5px;">'
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

    _LABEL_TO_ROLE = {
        "あなた:":     "user",
        "アシスタント:": "assistant",
        "システム:":   "system",
    }

    def _toggle_edit_mode(self):
        self.is_editing = not self.is_editing
        self.conversation_text.setReadOnly(not self.is_editing)

        if self.is_editing:
            self.edit_button.setText("編集終了")
            self.statusBar().showMessage(
                "編集モード: 会話を直接編集できます（画像情報は失われます）"
            )
            parts = []
            for msg in self.conversation_history:
                label = {"user": "あなた", "assistant": "アシスタント"}.get(
                    msg.role, "システム"
                )
                parts.append(f"{label}: {msg.text}")
            self.conversation_text.setPlainText("\n\n".join(parts))
        else:
            self.edit_button.setText("編集モード")
            self.statusBar().showMessage("編集モードを終了しました")
            self._sync_history_from_editor()

    def _sync_history_from_editor(self):
        """編集テキストを conversation_history に反映する。"""
        lines = self.conversation_text.toPlainText().split("\n")
        new_history: list[Message] = []
        current_role:  str | None = None
        current_lines: list[str]  = []

        def _flush():
            if current_role and current_lines:
                text = "\n".join(current_lines).strip()
                new_history.append(
                    Message(current_role, [{"type": "text", "text": text}])
                )

        for line in lines:
            matched = False
            for prefix, role in self._LABEL_TO_ROLE.items():
                if line.startswith(prefix):
                    _flush()
                    current_role  = role
                    current_lines = [line[len(prefix):].lstrip()]
                    matched = True
                    break
            if not matched and current_role is not None:
                current_lines.append(line)  # strip() しない（空白行を保持）

        _flush()
        self._carry_over_metadata(new_history)
        self.conversation_history = new_history
        self._redraw_conversation()
        self._update_usage_label()
        self._mark_dirty()

    def _carry_over_metadata(self, new_history: list[Message]):
        """
        編集はテキストしか往復しないため、モデル名・推論・使用量が失われる。
        先頭から role が一致している間は、対応する旧メッセージの情報を引き継ぐ。
        メッセージを増減させた場合、そこから先は引き継げない。
        """
        for new, old in zip(new_history, self.conversation_history):
            if new.role != old.role:
                break
            new.model     = old.model
            new.reasoning = old.reasoning
            new.usage     = old.usage
            new.timestamp = old.timestamp

    # ══════════════════════════════════════════════════════════
    # 保存・読み込み
    # ══════════════════════════════════════════════════════════

    def _clear_conversation(self):
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
        """指定パスへ書き出す。成功したら True。"""
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "version":       CONVERSATION_FORMAT_VERSION,
                    "session_start": self.session_start.isoformat(),
                    "saved_at":      datetime.now().isoformat(),
                    "model":         self.model_combo.currentText(),
                    "thinking_level": self.thinking_level_combo.currentText(),
                    "conversation":  [m.to_json() for m in self.conversation_history],
                }, f, ensure_ascii=False, indent=2)
        except Exception as exc:
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

    def _export_markdown(self):
        if self.is_editing:
            self._toggle_edit_mode()
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
            if saved_level in THINKING_LEVELS:
                self.thinking_level_combo.setCurrentText(saved_level)

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
        # 待ち時間は短く区切る（残ったスレッドはデーモン相当で放置してよい）。
        for worker in [self.worker, *self._retired_workers]:
            if worker is not None and worker.isRunning():
                worker.cancel()
        for worker in [self.worker, *self._retired_workers]:
            if worker is not None:
                worker.wait(1_000)
        if self._catalog_worker is not None:
            self._catalog_worker.wait(1_000)
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
    exit_code = app.exec_()

    # 受信待ちでブロックしたままのスレッドが残っていると、通常終了では
    # 実行中の QThread が破棄されて Qt が異常終了扱いにする。
    # 設定・会話の保存は closeEvent で済んでいるので、ここで打ち切る。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)


if __name__ == "__main__":
    main()