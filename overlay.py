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
#tag  { color: #39d3bb; font: 12px "Microsoft YaHei UI"; letter-spacing: 3px;
        background: transparent; border: none; }
#body { color: #e6f2f0; font: 16px/1.8 "Microsoft YaHei UI";
        background: transparent; border: none; }
#foot { color: #5d7a79; font: 12px "Microsoft YaHei UI";
        background: transparent; border: none; }
"""

MAX_W = 560          # 卡片最宽到这儿，再长的文字就换行往下长
PAD = 20             # 卡片内边距，正文据此限宽
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
        self.setMaximumWidth(MAX_W)

        card = QWidget()
        card.setObjectName("card")
        card.setStyleSheet(CARD_QSS)

        # 抬头和开场白都听界面里配的称呼，写死在这儿就换不了了
        from config import load as load_config
        _c = load_config()
        _title = _c.get("user_title") or "指挥官"
        _coach = _c.get("coach_name") or "教练"

        self.tag = QLabel(f"{_title}，{_coach}专用频道")
        self.tag.setObjectName("tag")
        self.body = QLabel(f"{_coach}正在回看这一波…")
        self.body.setObjectName("body")
        self.body.setWordWrap(True)
        self.body.setMaximumWidth(MAX_W - PAD * 2)  # 到限宽就换行，卡片往下长
        self.foot = QLabel("")
        self.foot.setObjectName("foot")
        self.foot.setWordWrap(True)

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
    def _relayout(self):
        """字变多了就重新量一遍尺寸，再贴回角落。
           少了这一步，卡片会停在弹出那一刻的高度，后面的字全被裁掉。"""
        self.adjustSize()
        self.move(self.place())

    def begin(self):
        """模型开腔了 —— 清掉"正在回看"，准备接字。"""
        self.body.setText("")
        self._relayout()

    def push(self, ch):
        """来一个字打一个字。模型的流式节奏就是打字机节奏，不额外加延迟。"""
        self.body.setText(self.body.text() + ch)
        self._relayout()

    def set_foot(self, text):
        self.foot.setText(text)
        self._relayout()

    def fail(self, msg):
        self.tag.setText("COACH · 出错了")
        self.tag.setStyleSheet("#tag{color:#e08b6a;letter-spacing:3px;}")
        self.body.setText(msg)
        self._relayout()

    # ---------------------------------------------------------- 位置与动画
    def place(self, corner="br"):
        scr = QApplication.primaryScreen().availableGeometry()
        if corner == "br":      # 右下：不挡准星，也不挡小地图
            x = scr.right() - self.width() - MARGIN
            y = scr.bottom() - self.height() - MARGIN
        else:                   # 右上
            x = scr.right() - self.width() - MARGIN
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


def run_with_overlay(call, args, d, frames, prompt, usage_cb=None, speaker=None):
    """开一个 Qt 界面跑完整流程：卡片滑入 → 逐字蹦出来 → 停留 → 淡出 → 退出。

    call:    真正的 API 调用函数，签名里必须接受 on_chunk/on_first/on_done/on_usage
    speaker: 传进来的 Speaker（已经在预热了）。有了它，模型吐字会同步喂给语音，
             而且卡片会等语音播完再淡出 —— 不然字念到一半卡片就消失了。
    """
    app = QApplication.instance() or QApplication(sys.argv)
    ov = CoachOverlay()
    ov.show()
    ov.slide_in()

    w = StreamWorker(call, args=args, d=d, frames=frames, prompt=prompt)

    def on_chunk(ch):
        ov.push(ch)
        if speaker is not None:
            speaker.feed(ch)      # 边生成边喂，攒够一句就开念

    w.chunk.connect(on_chunk)
    w.first.connect(lambda t: (ov.begin(), ov.set_foot(f"首字 {t:.1f}s")))
    w.usage.connect(lambda u: usage_cb(u) if usage_cb else None)

    def quit_soon(delay=0):
        QTimer.singleShot(delay, lambda: ov.fade_out(then=app.quit))

    def on_done(total, n):
        ov.set_foot(f"用时 {total:.1f}s · {n} 字")
        if speaker is not None:
            speaker.finish()       # 把剩下的零碎也念了
            # 卡片留着，等念完再走；万一语音那边出岔子，25 秒后也强制退出
            speaker.finished.connect(
                lambda t: (ov.set_foot(f"用时 {total:.1f}s · {n} 字 · 语音播完 {t:.1f}s"),
                           quit_soon(600)))
            QTimer.singleShot(25000, lambda: quit_soon(0))
        else:
            quit_soon(HOLD_MS)

    w.done.connect(on_done)
    w.failed.connect(lambda m: (
        ov.fail(m),
        QTimer.singleShot(6000, lambda: ov.fade_out(then=app.quit)),
    ))
    w.start()
    sys.exit(app.exec())
