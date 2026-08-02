# tests

`GUI.py` の自動テストです。アプリ本体はリポジトリ直下の `GUI.py` で、このフォルダは開発用です。

## 実行方法

```bash
pip install pytest
pytest
```

外部への通信を伴うテストを除く場合:

```bash
pytest -m "not network"
```

## 構成

| ファイル | 対象 |
|---|---|
| `conftest.py` | 共通の下ごしらえ（offscreen 起動、設定の隔離、ウィンドウ生成） |
| `test_rendering.py` | Markdown 描画と HTML サニタイズ |
| `test_message.py` | 会話データモデル（`Message`）と API 送信形式 |
| `test_streaming.py` | ストリーミング表示、キャンセル、二重送信の防止 |
| `test_catalog.py` | モデル一覧の取得・マージ、推論パラメータ |
| `test_features.py` | プリセット、再生成、コスト表示、添付 |
| `test_persistence.py` | 保存・読み込み・Ctrl+S・終了時の挙動 |
| `test_api_e2e.py` | ローカルの疑似 API サーバを立てた通し確認 |

## 前提

- ウィンドウは `QT_QPA_PLATFORM=offscreen` で画面に出さずに動かします（`conftest.py` が自動設定）。
- 設定（`QSettings`）は専用スコープへ隔離するため、実際の設定は書き換わりません。
- `test_api_e2e.py` は `127.0.0.1` に立てた疑似サーバへ接続します。OpenRouter へは接続しません。
- `network` マーカーの付いたテストだけが実際に OpenRouter（モデル一覧のみ、認証不要）へ接続します。

## 注意点

`QThread` を持ったウィンドウをガベージコレクトさせるとプロセスごと落ちるため、
`conftest.py` は生成したウィンドウの参照をセッション終了まで保持しています。
テストを追加する際は `window` / `make_window` フィクスチャを使ってください。
