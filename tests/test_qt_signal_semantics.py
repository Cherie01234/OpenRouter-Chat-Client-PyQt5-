"""
PyQt のシグナル切断の意味づけを固定する。

GUI.py のキャンセルは「シグナルを disconnect して、その場で表示を確定する」
方式に依存している。Qt 一般では、切断前にキューへ積まれた queued signal は
切断後にも配送されうる。しかし PyQt は @pyqtSlot の付かない Python callable へ
内部 QObject プロキシを作り、同一スレッドからの切断でそれを即時削除するため、
宛先の未配送イベントも一緒に消える。

このテストは A/B の対照実験で、その前提が成立し続けていることを確かめる。
A（@pyqtSlot 付き）が後着し、B（通常メソッド）が後着しなければ前提は有効。
B が後着するようになったら、リクエスト世代の判定が必要になる。
"""
import pytest
from PyQt5.QtCore import (
    QObject, QCoreApplication, QEvent, QMetaObject, QThread, Qt,
    PYQT_VERSION_STR, QT_VERSION_STR, pyqtSignal, pyqtSlot,
)

import GUI


class _Emitter(QObject):
    fired = pyqtSignal(str)

    @pyqtSlot()
    def emit_once(self):
        self.fired.emit("late")


class _Receiver(QObject):
    def __init__(self):
        super().__init__()
        self.plain_calls = []
        self.qt_calls = []

    def plain(self, value):
        """GUI.py のハンドラと同じ、通常の Python メソッド。"""
        self.plain_calls.append(value)

    @pyqtSlot(str)
    def qt_slot(self, value):
        """ネイティブ Qt スロットとなる正の対照。"""
        self.qt_calls.append(value)


def _queue_then_disconnect(slot, calls):
    """別スレッドで emit を完了させ、配送前に切断してから配送を試みる。"""
    emitter = _Emitter()
    thread = QThread()
    emitter.moveToThread(thread)
    thread.finished.connect(emitter.deleteLater)
    thread.start()
    try:
        # AutoConnection。emit 時は別スレッドなので QueuedConnection になる
        emitter.fired.connect(slot)
        # emit_once の完了まで待つ。こちらはイベントループを回していないので
        # fired はキューに積まれたまま残る
        QMetaObject.invokeMethod(emitter, "emit_once", Qt.BlockingQueuedConnection)
        assert calls == [], "配送前であるはずの時点で既に呼ばれている"

        emitter.fired.disconnect()          # 本番と同じ引数なし形式
        QCoreApplication.sendPostedEvents(None, QEvent.MetaCall)
    finally:
        thread.quit()
        thread.wait(2000)


@pytest.fixture(scope="module", autouse=True)
def report_versions():
    """依存している実装のバージョンを記録に残す。"""
    print(f"\nPyQt={PYQT_VERSION_STR} Qt={QT_VERSION_STR}")


class TestQueuedSignalAfterDisconnect:
    def test_decorated_slot_still_receives(self, qapp):
        """
        対照群。ここが後着しなければ、テスト装置自体が働いていない。
        """
        receiver = _Receiver()
        _queue_then_disconnect(receiver.qt_slot, receiver.qt_calls)
        assert receiver.qt_calls == ["late"], (
            "キューへ積めていない可能性がある。"
            f"PyQt={PYQT_VERSION_STR} Qt={QT_VERSION_STR}"
        )

    def test_plain_method_is_removed_by_disconnect(self, qapp):
        """
        GUI.py と同じ接続形態。ここが後着するようになったら、
        キャンセル処理にリクエスト世代の判定を入れる必要がある。
        """
        receiver = _Receiver()
        _queue_then_disconnect(receiver.plain, receiver.plain_calls)
        assert receiver.plain_calls == [], (
            "切断後にキュー済みシグナルが後着した。"
            "_detach_worker の前提が崩れている。"
            f"PyQt={PYQT_VERSION_STR} Qt={QT_VERSION_STR}"
        )


class TestHandlersAreNotDecorated:
    """
    上の前提は「ハンドラが通常の Python メソッドであること」に依存する。
    @pyqtSlot を付けるとネイティブ経路になり、後着が実在しうる。
    """

    @pytest.mark.parametrize("name", [
        "_on_chunk_received", "_on_stream_finished", "_handle_api_error",
    ])
    def test_handler_has_no_pyqtslot_decorator(self, name):
        handler = getattr(GUI.OpenRouterChatApp, name)
        # @pyqtSlot を付けると __pyqtSignature__ 等の属性が生える
        assert not hasattr(handler, "__pyqtSignature__"), (
            f"{name} に @pyqtSlot が付いている。"
            "リクエスト世代の判定なしでは切断後の後着を防げない。"
        )
