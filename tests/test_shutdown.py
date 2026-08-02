"""
終了処理を別プロセスで確認する。

止まりきらないスレッドが残ったときだけ強制終了へ切り替える設計なので、
同一プロセス内では検証できない（本当に os._exit すると pytest ごと死ぬ）。
子プロセスの終了コードで、どちらの経路を通ったかを判定する。
"""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import GUI

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_child(body: str, timeout: int = 60) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""
        import os, sys
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["OPENROUTER_API_KEY"] = "test-key-never-sent"
        sys.path.insert(0, {str(REPO_ROOT)!r})
        import GUI
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtGui import QCloseEvent

        app = QApplication([])
        GUI.OpenRouterChatApp._start_catalog_fetch = lambda self: None
        window = GUI.OpenRouterChatApp()
        {textwrap.indent(textwrap.dedent(body), " " * 8).strip()}
    """)
    return subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, timeout=timeout)


class TestExitPath:
    def test_clean_exit_uses_sys_exit(self):
        """スレッドが残っていなければ通常終了（atexit が走る）。"""
        result = run_child("""
            import atexit
            atexit.register(lambda: print("atexit ran"))
            event = QCloseEvent(); event.setAccepted(False)
            window.closeEvent(event)
            assert event.isAccepted()
            assert window.threads_pending is False
            GUI.finish_application(window, 0)
        """)
        assert result.returncode == 0, result.stderr[-2000:]
        assert "atexit ran" in result.stdout

    def test_stuck_worker_forces_exit(self):
        """
        止まらないスレッドが残った場合は強制終了へ切り替わり、
        通常終了と区別できる終了コードになる。
        """
        result = run_child("""
            import atexit
            atexit.register(lambda: print("atexit ran"))

            class StuckWorker:
                def isRunning(self):  return True
                def cancel(self):     pass
                def wait(self, msec): return False

            window.worker = StuckWorker()
            event = QCloseEvent(); event.setAccepted(False)
            window.closeEvent(event)
            assert event.isAccepted()
            assert window.threads_pending is True
            GUI.finish_application(window, 0)
        """)
        assert result.returncode == GUI.FORCED_EXIT_CODE, result.stderr[-2000:]
        # os._exit なので atexit は走らない（それが狙い）
        assert "atexit ran" not in result.stdout

    def test_close_after_cancel_exits_cleanly(self):
        """応答中にキャンセルしてから閉じても、通常終了で済むこと。"""
        result = run_child("""
            GUI.ApiWorker.start = lambda self: None
            window.api_key = "key"
            window.message_input.setPlainText("テスト")
            window.message_input.send_requested.emit()
            window._cancel_request()
            assert not window._is_busy()

            event = QCloseEvent(); event.setAccepted(False)
            window.closeEvent(event)
            assert window.threads_pending is False
            GUI.finish_application(window, 0)
        """)
        assert result.returncode == 0, result.stderr[-2000:]

    def test_unsaved_changes_block_close(self):
        """未保存のまま閉じようとすると、確認で止まること。"""
        result = run_child("""
            from PyQt5.QtWidgets import QMessageBox
            from GUI import Message

            class Stub:
                def button(self, *a): return self
                def setText(self, *a): pass
                def setDefaultButton(self, *a): pass
                def exec_(self): return QMessageBox.Cancel
            GUI.OpenRouterChatApp._make_dialog = lambda self, *a, **k: Stub()

            window.conversation_history = [Message.user("未保存の会話")]
            window._mark_dirty(True)
            event = QCloseEvent(); event.setAccepted(False)
            window.closeEvent(event)
            assert not event.isAccepted(), "未保存なのに閉じてしまった"
            print("blocked")
            GUI.finish_application(window, 0)
        """)
        assert result.returncode == 0, result.stderr[-2000:]
        assert "blocked" in result.stdout
