import sys
import os
import json
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QTextEdit, QLineEdit, QPushButton, QLabel, QCheckBox, QSpinBox,
                             QSplitter, QFrame, QMessageBox, QFileDialog, QStatusBar, QComboBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QTextCursor, QPalette, QColor
import requests
import html

# API呼び出しを別スレッドで実行するためのワーカークラス
class ApiWorker(QThread):
    finished = pyqtSignal(str, str)  # コンテンツと推論プロセスを返すシグナル
    error = pyqtSignal(str)  # エラーメッセージを返すシグナル

    def __init__(self, api_key, messages, use_reasoning, temperature, max_tokens, model):
        super().__init__()
        self.api_key = api_key
        self.messages = messages
        self.use_reasoning = use_reasoning
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.model = model
        self.url = "https://openrouter.ai/api/v1/chat/completions"

    def run(self):
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": self.model,
                "messages": self.messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens
            }
            
            # モデルに応じた推論パラメータの設定
            if self.use_reasoning:
                if "deepseek" in self.model:
                    # DeepSeekモデルの場合
                    data["reasoning"] = {"enabled": True}
                    data["reasoning"]["effort"] = "high"
                elif "grok" in self.model:
                    # Grokモデルの場合
                    data["reasoning"] = {"enabled": True}
            
            response = requests.post(self.url, headers=headers, json=data, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                message_content = result['choices'][0]['message']['content']
                
                # 推論プロセスの取得方法を修正
                reasoning = ""
                reasoning_tokens = 0
                
                if self.use_reasoning and ("deepseek" in self.model or "grok" in self.model):
                    # レスポンスの様々な場所をチェック
                    message_data = result['choices'][0]['message']
                    
                    # 推論トークン数を取得
                    usage = result.get('usage', {})
                    completion_details = usage.get('completion_tokens_details', {})
                    reasoning_tokens = completion_details.get('reasoning_tokens', 0)
                    
                    # DeepSeekモデルの場合の処理
                    if "deepseek" in self.model:
                        reasoning = message_data.get('reasoning', '')
                        if not reasoning:
                            reasoning = message_data.get('reasoning_content', '')
                        if not reasoning:
                            reasoning = message_data.get('reasoning_text', '')
                        if not reasoning and 'reasoning' in result:
                            reasoning = result.get('reasoning', '')
                    
                    # Grokモデルの場合の処理
                    elif "grok" in self.model:
                        reasoning = message_data.get('reasoning', '')
                        if not reasoning and 'reasoning' in result:
                            reasoning = result.get('reasoning', '')
                    
                    # 推論トークンが使用されているのにreasoningが空の場合
                    if not reasoning and reasoning_tokens > 0:
                        reasoning = f"（推論トークンが {reasoning_tokens} 使用されましたが、推論プロセスは提供されていません）"
                    
                    # 推論トークン情報を追加
                    if reasoning_tokens > 0:
                        reasoning_header = f"【推論トークン使用量: {reasoning_tokens}】\n\n"
                        reasoning = reasoning_header + reasoning
                
                self.finished.emit(message_content, reasoning)
            else:
                self.error.emit(f"APIエラー: {response.status_code} - {response.text}")
                
        except Exception as e:
            self.error.emit(f"例外が発生しました: {str(e)}")

# メインアプリケーションウィンドウ
class OpenRouterChatApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        self.conversation_history = []
        self.session_start = datetime.now()
        self.is_editing = False  # 編集モードかどうか
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle("OpenRouter Chat - PyQt5")
        self.setGeometry(100, 100, 1000, 700)
        
        # 中央ウィジェットとメインレイアウト
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # ステータスバー
        self.statusBar().showMessage("準備完了")
        
        # 会話表示エリア
        self.conversation_text = QTextEdit()
        self.conversation_text.setReadOnly(True)  # 初期状態は読み取り専用
        self.conversation_text.setFont(QFont("Arial", 10))
        
        # 推論表示エリア
        self.reasoning_text = QTextEdit()
        self.reasoning_text.setReadOnly(True)
        self.reasoning_text.setFont(QFont("Arial", 9))
        self.reasoning_text.setMaximumHeight(150)
        
        # 会話と推論エリアのスタイルを設定（文字色を白に）
        text_edit_style = """
            QTextEdit {
                color: white;
                background-color: #252525;
                border: 1px solid #444444;
            }
            QTextEdit:editable {
                background-color: #353535;
                border: 2px solid #2B5B84;
            }
        """

        self.conversation_text.setStyleSheet(text_edit_style)
        self.reasoning_text.setStyleSheet(text_edit_style)
        
        # 入力エリア
        input_frame = QFrame()
        input_frame.setFrameShape(QFrame.StyledPanel)
        input_layout = QVBoxLayout(input_frame)
        
        # メッセージ入力（複数行対応）
        self.message_input = QTextEdit()
        self.message_input.setPlaceholderText("メッセージを入力してください...")
        self.message_input.setMaximumHeight(80)  # 高さを設定

        # 入力フィールドのスタイルを設定（文字色を黒に）
        input_style = """
            QTextEdit, QSpinBox, QComboBox {
                color: black;
                background-color: white;
                border: 1px solid #CCCCCC;
                padding: 3px;
            }
            QTextEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 2px solid #2B5B84;
            }
        """

        self.message_input.setStyleSheet(input_style)
        input_layout.addWidget(self.message_input)
        
        # 設定パネル
        settings_layout = QHBoxLayout()
        
        # モデル選択コンボボックスを追加
        settings_layout.addWidget(QLabel("モデル:"))
        self.model_combo = QComboBox()

	# 利用可能なLLMモデル一覧
	# OpenRouterに対応したモデル名を追加することで、
	# 新しいLLMを簡単に選択・利用できるようになります
        self.model_combo.addItems([
            "deepseek/deepseek-v3.2",
            "deepseek/deepseek-v3.2-exp",
            "x-ai/grok-4.1-fast"
        ])
        self.model_combo.setStyleSheet(input_style)
        settings_layout.addWidget(self.model_combo)
        
        # 推論表示チェックボックス（DeepSeek/Grok専用）
        self.reasoning_checkbox = QCheckBox("推論プロセスを表示")
        self.reasoning_checkbox.setChecked(True)
        settings_layout.addWidget(self.reasoning_checkbox)
        
        # モデル変更時のイベント接続
        self.model_combo.currentTextChanged.connect(self.on_model_changed)
        
        # 温度設定のラベル
        settings_layout.addWidget(QLabel("ランダム性:"))
        self.temperature_spin = QSpinBox()
        self.temperature_spin.setRange(0, 20)
        self.temperature_spin.setValue(7)
        self.temperature_spin.setSuffix(" (×0.1)")
        self.temperature_spin.setStyleSheet(input_style)
        settings_layout.addWidget(self.temperature_spin)
        
        settings_layout.addWidget(QLabel("最大トークン:"))
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(100, 10000)
        self.max_tokens_spin.setValue(4000)
        self.max_tokens_spin.setStyleSheet(input_style)
        settings_layout.addWidget(self.max_tokens_spin)
        
        settings_layout.addStretch()
        input_layout.addLayout(settings_layout)
        
        # ボタンパネル
        button_layout = QHBoxLayout()

        self.send_button = QPushButton("送信")
        self.send_button.clicked.connect(self.send_message)
        button_layout.addWidget(self.send_button)

        self.clear_button = QPushButton("会話をクリア")
        self.clear_button.clicked.connect(self.clear_conversation)
        button_layout.addWidget(self.clear_button)

        self.save_button = QPushButton("会話を保存")
        self.save_button.clicked.connect(self.save_conversation)
        button_layout.addWidget(self.save_button)
        
        # 会話読み込みボタン
        self.load_button = QPushButton("会話を読み込み")
        self.load_button.clicked.connect(self.load_conversation)
        button_layout.addWidget(self.load_button)
        
        # 編集モード切り替えボタン
        self.edit_button = QPushButton("編集モード")
        self.edit_button.clicked.connect(self.toggle_edit_mode)
        self.edit_button.setCheckable(True)  # トグルボタンとして設定
        button_layout.addWidget(self.edit_button)

        # ボタンのスタイルを設定
        button_style = """
            QPushButton {
                color: white;
                background-color: #2B5B84;
                border: 1px solid #1E415D;
                padding: 5px;
                border-radius: 3px;
                font-family: "MS UI Gothic";
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #3A7CBE;
            }
            QPushButton:pressed {
                background-color: #1E415D;
            }
            QPushButton:checked {
                background-color: #BE2B2B;
            }
            QPushButton:disabled {
                background-color: #555555;
                color: #AAAAAA;
            }
        """

        self.send_button.setStyleSheet(button_style)
        self.clear_button.setStyleSheet(button_style)
        self.save_button.setStyleSheet(button_style)
        self.load_button.setStyleSheet(button_style)
        self.edit_button.setStyleSheet(button_style)

        # フォントも設定（日本語表示用）
        font = QFont("MS UI Gothic", 9)
        self.send_button.setFont(font)
        self.clear_button.setFont(font)
        self.save_button.setFont(font)
        self.load_button.setFont(font)
        self.edit_button.setFont(font)

        input_layout.addLayout(button_layout)
        
        # スプリッターで会話と推論を分割
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.conversation_text)
        splitter.addWidget(self.reasoning_text)
        splitter.setSizes([550, 150])
        
        # メインレイアウトに追加
        main_layout.addWidget(splitter)
        main_layout.addWidget(input_frame)
        
        # 初期状態の設定
        self.on_model_changed(self.model_combo.currentText())
        
        # APIキーが設定されていない場合の警告
        if not self.api_key:
            QMessageBox.warning(self, "APIキー", 
                               "OPENROUTER_API_KEY環境変数が設定されていません。\n"
                               "設定後、アプリケーションを再起動してください。")
    
    def on_model_changed(self, model_name):
        """モデルが変更されたときの処理"""
        if "deepseek" in model_name or "grok" in model_name:
            # DeepSeekまたはGrokモデルの場合、推論機能を有効化
            self.reasoning_checkbox.setEnabled(True)
            self.reasoning_text.setVisible(True)
            if "deepseek" in model_name:
                model_display_name = "DeepSeek"
            else:
                model_display_name = "Grok"
            self.statusBar().showMessage(f"{model_display_name}モデル: 推論機能が利用できます")
        else:
            # 他のモデルの場合、推論機能を無効化
            self.reasoning_checkbox.setEnabled(False)
            self.reasoning_text.setVisible(False)
            self.statusBar().showMessage(f"{model_name.split('/')[0]}モデル: 推論機能は利用できません")
    
    def toggle_edit_mode(self):
        # 編集モードの切り替え
        self.is_editing = not self.is_editing
        self.conversation_text.setReadOnly(not self.is_editing)
        
        if self.is_editing:
            self.edit_button.setText("編集終了")
            self.statusBar().showMessage("編集モード: 会話内容を直接編集できます")
            
            # --- 修正箇所 ---
            # conversation_history から安定したプレーンテキストを作成して編集用にする
            parts = []
            for msg in self.conversation_history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user":
                    label = "あなた:"
                elif role == "assistant":
                    # 編集用には常に中立的なラベルを使う（パースを安定させるため）
                    label = "アシスタント:"
                else:
                    label = "システム:"
                # メッセージ間は一行の空行（\n\n）で区切る（安定して再パース可能）
                parts.append(f"{label} {content}")
            plain_text = "\n\n".join(parts)
            # 末尾の余分な空行は削ってからセット
            plain_text = plain_text.rstrip("\n")
            self.conversation_text.setPlainText(plain_text)
            # --- /修正箇所 ---
        else:
            self.edit_button.setText("編集モード")
            self.statusBar().showMessage("編集モードを終了しました")
            
            # 編集内容を会話履歴に反映
            self.update_conversation_from_edit()

    
    def update_conversation_from_edit(self):
        # 編集された内容を会話履歴に反映
        edited_text = self.conversation_text.toPlainText()
        
        # 会話を再解析して履歴を更新（簡易実装）
        lines = edited_text.split('\n')
        new_history = []
        current_role = None
        current_content = []
        
        for line in lines:
            if line.startswith('あなた:'):
                if current_role is not None and current_content:
                    new_history.append({"role": current_role, "content": "\n".join(current_content)})
                current_role = "user"
                current_content = [line.replace('あなた:', '', 1).strip()]
            elif line.startswith('アシスタント:'):
                if current_role is not None and current_content:
                    new_history.append({"role": current_role, "content": "\n".join(current_content)})
                current_role = "assistant"
                current_content = [line.replace('アシスタント:', '', 1).strip()]
            elif line.startswith('システム:'):
                if current_role is not None and current_content:
                    new_history.append({"role": current_role, "content": "\n".join(current_content)})
                current_role = "system"
                current_content = [line.replace('システム:', '', 1).strip()]
            elif current_role is not None:
                current_content.append(line.strip())
        
        # 最後のメッセージを追加
        if current_role is not None and current_content:
            new_history.append({"role": current_role, "content": "\n".join(current_content)})
        
        # 会話履歴を更新
        self.conversation_history = new_history
        
        # HTML形式で再表示
        self.conversation_text.clear()
        for message in self.conversation_history:
            role = message.get("role", "")
            content = message.get("content", "")
            
            if role == "user":
                self.append_to_conversation("あなた", content, False)
            elif role == "assistant":
                # モデル名に応じて表示名を変更
                model_name = self.model_combo.currentText()
                if "deepseek" in model_name:
                    self.append_to_conversation("DeepSeek", content, False)
                elif "grok" in model_name:
                    self.append_to_conversation("Grok", content, False)
                else:
                    self.append_to_conversation("アシスタント", content, False)
            elif role == "system":
                self.append_to_conversation("システム", content, False)
    
    def send_message(self):
        # 編集モードの場合は終了する
        if self.is_editing:
            self.toggle_edit_mode()
        
        # QTextEditからテキストを取得
        message = self.message_input.toPlainText().strip()
        if not message:
            return
            
        # 入力欄をクリア
        self.message_input.clear()
        
        # 会話履歴に追加
        self.conversation_history.append({"role": "user", "content": message})
        
        # 会話表示エリアにユーザーメッセージを追加
        self.append_to_conversation("あなた", message)
        
        # ステータスバーを更新
        selected_model = self.model_combo.currentText()
        self.statusBar().showMessage(f"{selected_model} で応答を待っています...")
        
        # API呼び出しを別スレッドで実行
        self.worker = ApiWorker(
            self.api_key,
            self.conversation_history,
            self.reasoning_checkbox.isChecked(),
            self.temperature_spin.value() / 10.0,  # 0.1単位で設定
            self.max_tokens_spin.value(),
            self.model_combo.currentText()  # 選択されたモデルを渡す
        )
        self.worker.finished.connect(self.handle_api_response)
        self.worker.error.connect(self.handle_api_error)
        self.worker.start()
        
        # 送信ボタンを無効化
        self.send_button.setEnabled(False)
    
    def handle_api_response(self, content, reasoning):
        # 会話履歴に追加
        self.conversation_history.append({"role": "assistant", "content": content})
        
        # モデル名に応じて表示名を変更
        model_name = self.model_combo.currentText()
        if "deepseek" in model_name:
            self.append_to_conversation("DeepSeek", content)
        elif "grok" in model_name:
            self.append_to_conversation("Grok", content)
        else:
            self.append_to_conversation("アシスタント", content)
        
        # 推論表示エリアを更新（DeepSeekまたはGrokモデルの場合のみ）
        if reasoning:
            self.reasoning_text.setPlainText(reasoning)
        else:
            # 推論機能が無効または推論プロセスが提供されていない場合
            model_name = self.model_combo.currentText()
            if "deepseek" in model_name or "grok" in model_name:
                self.reasoning_text.setPlainText("推論プロセスは提供されていません")
            else:
                self.reasoning_text.setPlainText("このモデルは推論機能をサポートしていません")
        
        # ステータスバーを更新
        self.statusBar().showMessage("応答を受信しました")
        
        # 送信ボタンを再有効化
        self.send_button.setEnabled(True)
    
    def handle_api_error(self, error_message):
        # エラーメッセージを表示
        self.append_to_conversation("システム", f"エラー: {error_message}")
        
        # ステータスバーを更新
        self.statusBar().showMessage(f"エラー: {error_message}")
        
        # 送信ボタンを再有効化
        self.send_button.setEnabled(True)
    
    def append_to_conversation(self, sender, message, scroll=True, add_separator=False):
        # 送信者に応じて色を変更
        if sender == "あなた":
            prefix = "<font color='lightblue'><b>あなた:</b></font> "
        elif sender == "DeepSeek":
            prefix = "<font color='lightgreen'><b>DeepSeek:</b></font> "
        elif sender == "Grok":
            prefix = "<font color='orange'><b>Grok:</b></font> "
        else:
            prefix = "<font color='salmon'><b>システム:</b></font> "

        cursor = self.conversation_text.textCursor()
        cursor.movePosition(QTextCursor.End)

        # セパレーター行（会話間に空白を入れたいときだけ）
        if add_separator and self.conversation_text.toPlainText():
            cursor.insertHtml("<br><br>")

        # メッセージ末尾の余分な改行を削る
        cleaned = message.rstrip("\n")
        escaped = html.escape(cleaned).replace("\n", "<br>")
        cursor.insertHtml(prefix + escaped)

        # --- ★ ここを追加 → 最後に1行だけ空行を追加 ---
        # 編集モードでは追加しない（表示崩れ防止）
        if not self.is_editing:
            cursor.insertHtml("<br><br>")
        # ----------------------------------------------------

        if scroll:
            self.conversation_text.ensureCursorVisible()

    
    def clear_conversation(self):
        # 編集モードの場合は終了する
        if self.is_editing:
            self.toggle_edit_mode()
            
        # 会話履歴と表示をクリア
        self.conversation_history = []
        self.conversation_text.clear()
        self.reasoning_text.clear()
        self.statusBar().showMessage("会話をクリアしました")
    
    def save_conversation(self):
        # 編集モードの場合は終了する
        if self.is_editing:
            self.toggle_edit_mode()
            
        # ファイル保存ダイアログを表示
        options = QFileDialog.Options()
        timestamp = self.session_start.strftime("%Y%m%d_%H%M%S")
        default_filename = f"openrouter_conversation_{timestamp}.json"
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "会話を保存", default_filename, 
            "JSON Files (*.json);;All Files (*)", options=options)
        
        if filename:
            try:
                # 会話データを準備（使用モデルも保存）
                data = {
                    "session_start": self.session_start.isoformat(),
                    "saved_at": datetime.now().isoformat(),
                    "model": self.model_combo.currentText(),
                    "conversation": self.conversation_history
                }
                
                # JSONファイルに保存
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                self.statusBar().showMessage(f"会話を {filename} に保存しました")
                
            except Exception as e:
                QMessageBox.critical(self, "保存エラー", f"ファイルの保存中にエラーが発生しました: {str(e)}")
    
    def load_conversation(self):
        # 編集モードの場合は終了する
        if self.is_editing:
            self.toggle_edit_mode()
            
        # ファイル読み込みダイアログを表示
        options = QFileDialog.Options()
        filename, _ = QFileDialog.getOpenFileName(
            self, "会話を読み込み", "", 
            "JSON Files (*.json);;All Files (*)", options=options)
        
        if filename:
            try:
                # JSONファイルから読み込み
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 会話履歴を復元
                self.conversation_history = data.get("conversation", [])
                
                # モデル情報があれば復元
                saved_model = data.get("model", "")
                if saved_model and saved_model in [self.model_combo.itemText(i) for i in range(self.model_combo.count())]:
                    self.model_combo.setCurrentText(saved_model)
                
                # 会話表示を更新
                self.conversation_text.clear()
                for message in self.conversation_history:
                    role = message.get("role", "")
                    content = message.get("content", "")
                    
                    if role == "user":
                        self.append_to_conversation("あなた", content, False)
                    elif role == "assistant":
                        # 保存時のモデルに応じて表示名を変更
                        if "deepseek" in saved_model:
                            self.append_to_conversation("DeepSeek", content, False)
                        elif "grok" in saved_model:
                            self.append_to_conversation("Grok", content, False)
                        else:
                            self.append_to_conversation("アシスタント", content, False)
                    elif role == "system":
                        self.append_to_conversation("システム", content, False)
                
                # 最後にスクロール
                self.conversation_text.ensureCursorVisible()
                
                self.statusBar().showMessage(f"会話を {filename} から読み込みました")
                
            except Exception as e:
                QMessageBox.critical(self, "読み込みエラー", f"ファイルの読み込み中にエラーが発生しました: {str(e)}")
    
    def closeEvent(self, event):
        # 編集モードの場合は終了する
        if self.is_editing:
            self.toggle_edit_mode()
            
        # 確認ダイアログのスタイルを設定
        dialog_style = """
            QMessageBox {
                background-color: #353535;
            }
            QMessageBox QLabel {
                color: white;
                font-size: 12px;
            }
            QMessageBox QPushButton {
                color: white;
                background-color: #2B5B84;
                border: 1px solid #1E415D;
                padding: 5px 10px;
                border-radius: 3px;
                min-width: 80px;
                font-family: "MS UI Gothic";
            }
            QMessageBox QPushButton:hover {
                background-color: #3A7CBE;
            }
            QMessageBox QPushButton:pressed {
                background-color: #1E415D;
            }
        """
        
        # カスタムメッセージボックスを作成
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("確認")
        msg_box.setText("会話を保存しますか？")
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        
        # ボタンのテキストを設定（日本語化）
        msg_box.button(QMessageBox.Yes).setText("はい")
        msg_box.button(QMessageBox.No).setText("いいえ")
        msg_box.button(QMessageBox.Cancel).setText("キャンセル")
        
        msg_box.setDefaultButton(QMessageBox.Yes)
        msg_box.setStyleSheet(dialog_style)
        
        # ダイアログを表示して結果を取得
        result = msg_box.exec_()
        
        if result == QMessageBox.Yes:
            self.save_conversation()
            event.accept()
        elif result == QMessageBox.No:
            event.accept()
        else:
            event.ignore()

# アプリケーションのエントリーポイント
def main():
    app = QApplication(sys.argv)
    
    # ダークテーマの適用（オプション）
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.WindowText, Qt.white)
    palette.setColor(QPalette.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ToolTipBase, Qt.white)
    palette.setColor(QPalette.ToolTipText, Qt.white)
    palette.setColor(QPalette.Text, Qt.black)
    palette.setColor(QPalette.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ButtonText, Qt.white)
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, Qt.black)
    app.setPalette(palette)
    
    # メインウィンドウの作成と表示
    window = OpenRouterChatApp()
    window.show()
    
    # アプリケーションの実行
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
