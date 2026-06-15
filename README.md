# OpenRouter Chat Client (PyQt5)

![demo](images/demo.gif)

![app_screen_layout](images/app_screen_layout.png)

## 概要

OpenRouter API を利用した、デスクトップ向けチャットクライアントアプリです。
PyQt5 を用いて GUI を構築し、複数の LLM モデル（DeepSeek / Grok / Gemini）を切り替えて利用できます。

個人学習および技術検証を目的として開発しました。

---

## 使用技術

- 言語：Python 3.x
- GUI：PyQt5
- HTTP通信：requests（ストリーミング対応）
- Markdownレンダリング：markdown（オプション）
- 外部API：OpenRouter API
- データ保存形式：JSON / Markdown

---

## 主な機能

### チャット・表示
- チャット形式での LLM との対話（ストリーミング表示）
- Markdown レンダリング（コードブロック・テーブル対応、`markdown` ライブラリ必須）
- 推論プロセス・推論トークン数の表示（対応モデルのみ）
- トークン使用量の表示（入力 / 出力）
- Ctrl+ホイールでフォントサイズ変更
- 右クリックメニューによるテキストコピー（選択範囲 / 全文）

### モデル・推論設定
- モデル切り替え（DeepSeek / Grok / Gemini）
- 思考レベル選択（minimal / low / medium / high）※Gemini のみ
- ランダム性（temperature）・最大トークン数の設定
- 設定の自動保存（QSettings）

### 入力・操作
- Ctrl+Enter でメッセージ送信
- 画像の添付送信（PNG / JPG / BMP / GIF）
- システムプロンプト入力（折りたたみ式）
- 会話の直接編集モード
- リクエストのキャンセル
- APIエラー時の再試行ダイアログ
- Ctrl+F でインライン検索（前後ナビゲーション対応）

### 保存・読み込み
- 会話履歴の保存 / 読み込み（JSON）
- Markdown ファイルとしてエクスポート

### UI
- ダークテーマ対応 UI
- 非同期 API 通信（QThread 使用）

---

## 起動方法

### 1. リポジトリをクローン

```bash
git clone https://github.com/Cherie01234/OpenRouter-Chat-Client-PyQt5-.git
cd OpenRouter-Chat-Client-PyQt5-
```

### 2. 必要なライブラリをインストール

```bash
pip install PyQt5 requests markdown
```

> `markdown` はオプションです。インストールしない場合、Markdown レンダリングは無効になりますが、他の機能は通常通り動作します。

### 3. 環境変数を設定

OpenRouter の API キーを環境変数に設定してください。

#### Windows (PowerShell)

```powershell
setx OPENROUTER_API_KEY "your_api_key_here"
```

#### macOS / Linux

```bash
export OPENROUTER_API_KEY="your_api_key_here"
```

### 4. アプリケーションを起動

```bash
python GUI.py
```

---

## 対応モデル

| 表示名 | モデルID | 推論機能 | 思考レベル設定 |
|--------|----------|----------|----------------|
| DeepSeek | `deepseek/deepseek-v4-pro` | ✅ | － |
| Grok | `x-ai/grok-4.3` | ✅ | － |
| Gemini | `google/gemini-3-flash-preview` | ✅ | ✅ |

新しいモデルを追加するには、コード冒頭の `MODEL_CONFIGS` 辞書にエントリを追加するだけで対応できます。

---

## ショートカットキー

| キー | 動作 |
|------|------|
| Ctrl+Enter | メッセージ送信 |
| Ctrl+F | 会話内検索（検索ダイアログを開く） |
| Ctrl+ホイール | 会話エリアのフォントサイズ変更 |

---

## 補足

- API キーはコード内に含まれておらず、環境変数から読み込む仕様です。
- 個人開発のため、OpenRouter API の仕様変更により動作しなくなる可能性があります。
- 設定（モデル・思考レベル・temperature・最大トークン数・システムプロンプト）はアプリ終了時に自動保存されます。
