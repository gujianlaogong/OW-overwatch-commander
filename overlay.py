# -*- coding: utf-8 -*-
"""
浮在游戏上面的复盘卡片（Qt 实现）

形态：桌面级置顶窗口 —— 游戏不知道它存在，跟桌面贴张便利贴一个性质。
绝对不做"注入游戏进程内部绘制"那套（那是外挂同款，反作弊主要查的就是它）。

三个效果都是 Windows 原生支持的，上一轮已逐项实测通过：
    置顶            Qt.WindowStaysOnTopHint
    逐像素透明       Qt.WA_TranslucentBackground（卡片本身可半透明，圆角无黑边）
    鼠标穿透         Qt.WindowTransparentForInput（鼠标直接穿过去，不抢游戏操作）
"""
import os
import sys

from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation,
                            Qt, QTimer, Signal, QThread)
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

HERE = os.path.dirname(os.path.abspath(__file__))

CARD_QSS = """
#card {
    background: rgba(13, 19, 25, 220);
    border: 1px solid rgba(43, 212, 189, 64);
    border-radius: 14px;
}
#tag  { color: #39d3bb; font: 11px "Microsoft YaHei UI"; letter-spacing: 3px;
        background: transparent; border: none; }
#body { color: #e6f2f0; font: 15px/1.75 "Microsoft YaHei UI";
        background: transparent; border: none; }
#foot { color: #5d7a79; font: 11px "Microsoft YaHei UI";
        background: transparent; border: none; }
"""

WIDTH = 470
MARGIN = 22          # 距屏幕边缘
HOLD_MS = 7000       # 说完后停留多久再淡出
FADE_MS = 700


class CoachOverlay(QWidget):
    """一个卡片窗口。只暴露几个动作，复杂的都在里面。"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint        # 无边框
            | Qt.WindowStaysOnTopHint     # 永远置顶
            | Qt.Tool                     # 不进任务栏、不进 Alt+Tab
            | Qt.WindowTransparentForInput  # 鼠标穿透
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)  # 双保险
        self.setWindowTitle("Coach")

        card = QWidget()
        card.setObjectName("card")
        card.setStyleSheet(CARD_QSS)

        self.tag = QLabel("COACH · 战后复盘")
        self.tag.setObjectName("tag")
        self.body = QLabel("正在回看这一波…")
        self.body.setObjectName("body")
        self.body.setWordWrap(True)
        self.foot = QLabel("")
        self.foot.setObjectName("foot")

        inner = QVBoxLayout(card)
        inner.setContentsMargins(20, 15, 20, 14)
        inner.setSpacing(6)
        inner.addWidget(self.tag)
        inner.addWidget(self.body)
        inner.addWidget(self.foot)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        self.anim_in = None
        self.anim_out = None

    # ---------------------------------------------------------- 内容
    def begin(self):
        """模型开腔了 —— 清掉"正在回看"，准备接字。"""
        self.body.setText("")

    def push(self, ch):
        """来一个字打一个字。模型的流式节奏就是打字机节奏，不额外加延迟。"""
        self.body.setText(self.body.text() + ch)

    def set_foot(self, text):
        self.foot.setText(text)

    def fail(self, msg):
        self.tag.setText("COACH · 出错了")
        self.tag.setStyleSheet("#tag{color:#e08b6a;letter-spacing:3px;}")
        self.body.setText(msg)

    # ---------------------------------------------------------- 位置与动画
    def place(self, corner="br"):
        scr = QApplication.primaryScreen().availableGeometry()
        if corner == "br":      # 右下：不挡准星，也不挡小地图
            x = scr.right() - WIDTH - MARGIN
            y = scr.bottom() - self.height() - MARGIN
        else:                   # 右上
            x = scr.right() - WIDTH - MARGIN
            y = scr.top() + MARGIN
        return QPoint(x, y)

    def slide_in(self):
        self.adjustSize()
        end = self.place()
        self.move(end.x() + 40, end.y())   # 从右边 40px 处起步

        self.setWindowOpacity(0.0)
        self.anim_in = QPropertyAnimation(self, b"windowOpacity")
        self.anim_in.setDuration(260)
        self.anim_in.setStartValue(0.0)
        self.anim_in.setEndValue(1.0)
        self.anim_in.setEasingCurve(QEasingCurve.OutCubic)
        self.anim_in.start()

        # 位置也滑一下，纯位移没有 Widget 属性可做动画，用定时器补一个滑入
        self._step = 0

        def slide():
            self._step += 1
            t = min(1.0, self._step / 13.0)
            e = 1 - (1 - t) ** 3           # OutCubic
            self.move(end.x() + int(40 * (1 - e)), end.y())
            if t < 1.0:
                QTimer.singleShot(16, slide)
        QTimer.singleShot(16, slide)

    def fade_out(self, then=None):
        self.anim_out = QPropertyAnimation(self, b"windowOpacity")
        self.anim_out.setDuration(FADE_MS)
        self.anim_out.setStartValue(self.windowOpacity())
        self.anim_out.setEndValue(0.0)
        self.anim_out.setEasingCurve(QEasingCurve.InCubic)
        if then:
            self.anim_out.finished.connect(then)
        self.anim_out.start()


class StreamWorker(QThread):
    """在子线程里等模型，来一个字就抛信号给主线程。
       不这么做的话，网络等待会把 GUI 卡住。"""
    chunk = Signal(str)
    first = Signal(float)     # 首字延迟
    done = Signal(float, int)  # 总耗时、总字数
    usage = Signal(object)
    failed = Signal(str)

    def __init__(self, caller, **kw):
        super().__init__()
        self.caller = caller   # 真正的 API 调用函数
        self.kw = kw

    def run(self):
        try:
            self.caller(
                on_chunk=lambda c: self.chunk.emit(c),
                on_first=lambda t: self.first.emit(t),
                on_done=lambda t, n: self.done.emit(t, n),
                on_usage=lambda u: self.usage.emit(u),
                **self.kw,
            )
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {str(e)[:160]}")


def run_with_overlay(call, args, d, frames, prompt, usage_cb=None):
    """开一个 Qt 界面跑完整流程：卡片滑入 → 逐字蹦出来 → 停留 → 淡出 → 退出。

    call: 真正的 API 调用函数，签名里必须接受 on_chunk/on_first/on_done/on_usage
    """
    app = QApplication.instance() or QApplication(sys.argv)
    ov = CoachOverlay()
    ov.show()
    ov.slide_in()

    w = StreamWorker(call, args=args, d=d, frames=frames, prompt=prompt)
    w.chunk.connect(ov.push)
    w.first.connect(lambda t: (ov.begin(), ov.set_foot(f"首字 {t:.1f}s")))
    w.usage.connect(lambda u: usage_cb(u) if usage_cb else None)

    def on_done(total, n):
        ov.set_foot(f"用时 {total:.1f}s · {n} 字")
        QTimer.singleShot(HOLD_MS, lambda: ov.fade_out(then=app.quit))

    w.done.connect(on_done)
    w.failed.connect(lambda m: (
        ov.fail(m),
        QTimer.singleShot(6000, lambda: ov.fade_out(then=app.quit)),
    ))
    w.start()
    sys.exit(app.exec())
