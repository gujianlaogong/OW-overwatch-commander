# -*- coding: utf-8 -*-
"""
第一步（换 PySide6 重验）：窗口三要素
    1. 置顶  2. 背景透明（逐像素 alpha）  3. 鼠标穿透
老规矩：自动跑完自动关，打印结论 + 存整屏截图。
"""
import ctypes
import ctypes.wintypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000

u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.GetWindowLongW.restype = ctypes.c_long
u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
u32.WindowFromPoint.restype = ctypes.c_void_p
u32.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
u32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.RECT)]

# ---------------------------------------------------------------- 抓屏工具
import mss
from PIL import Image


def grab(x, y, w, h):
    with mss.MSS() as sct:
        raw = sct.grab({"left": x, "top": y, "width": w, "height": h})
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def avg(img):
    px = list(img.getdata())
    n = len(px)
    return tuple(sum(p[i] for p in px) / n for i in range(3))


def dist(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / 3


def save_screen(path):
    with mss.MSS() as sct:
        sct.shot(output=path)
    return path

# ---------------------------------------------------------------- 界面
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

CARD_QSS = """
#card {
    background: rgba(16, 24, 31, 220);
    border: 1px solid rgba(43, 212, 189, 90);
    border-radius: 14px;
    padding: 14px 20px;
    color: #e8f4f2;
    font: 15px "Microsoft YaHei UI";
}
"""


def build_ui():
    win = QWidget()
    # 三个 flag：无边框 / 永远置顶 / 不出现在任务栏；再要鼠标穿透
    win.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                       | Qt.Tool | Qt.WindowTransparentForInput)
    # 关键属性：逐像素透明（整个窗口的背景不画，卡片自己带半透明）
    win.setAttribute(Qt.WA_TranslucentBackground)
    win.setAttribute(Qt.WA_TransparentForMouseEvents)  # 双保险

    card = QLabel()
    card.setObjectName("card")
    card.setText(
        "<span style='color:#39d3bb;font-size:12px;letter-spacing:2px;'>OVERLAY PROBE</span>"
        "<br>浮起来了就说明三件事都成立：置顶 / 透明 / 鼠标能穿过去"
    )
    card.setStyleSheet(CARD_QSS)
    card.setTextFormat(Qt.RichText)

    lay = QVBoxLayout(win)
    lay.setContentsMargins(18, 18, 18, 18)
    lay.addWidget(card)

    win.move(260, 220)
    win.resize(560, 160)
    return win


def probe(win, log, base_empty, base_card, app):
    hwnd = int(win.winId())

    rect = ctypes.wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rect))
    log.append((f"句柄 {hwnd}  物理位置 {rect.left},{rect.top}  "
                f"尺寸 {rect.right - rect.left}x{rect.bottom - rect.top}", True))

    back = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    log.append(("鼠标穿透 WS_EX_TRANSPARENT", bool(back & WS_EX_TRANSPARENT)))
    log.append(("不进任务栏 WS_EX_TOOLWINDOW", bool(back & WS_EX_TOOLWINDOW)))
    log.append(("分层窗口 WS_EX_LAYERED（Qt 逐像素透明走这条路）", bool(back & WS_EX_LAYERED)))

    # 硬验证：窗口中心坐标上最顶层窗口是谁。穿透生效 = 身后的窗口
    cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    top = u32.WindowFromPoint(ctypes.wintypes.POINT(cx, cy))
    if top == hwnd:
        log.append(("鼠标穿透【未生效】", False))
    else:
        log.append((f"鼠标穿透【生效】—— 该坐标命中的是身后窗口 ({top})", True))

    # 像素验证（用真实物理位置算采样点，避免 DPI 缩放错位）
    L, T = rect.left, rect.top
    empty_now = avg(grab(L + 3, T + 3, 10, 10))            # 卡片外 → 应透出桌面
    card_now = avg(grab(L + 55, T + 45, 220, 16))          # 卡片内 → 应是深色玻璃

    d_empty = dist(base_empty, empty_now)
    d_card = dist(base_card, card_now)
    log.append((f"卡片外：放窗前 {tuple(round(v) for v in base_empty)} → "
                f"放窗后 {tuple(round(v) for v in empty_now)}（差 {d_empty:.1f}）", True))
    log.append((f"卡片内：放窗前 {tuple(round(v) for v in base_card)} → "
                f"放窗后 {tuple(round(v) for v in card_now)}（差 {d_card:.1f}）", True))

    if d_empty < 12:
        log.append(("背景透明【生效】—— 卡片外透出了身后的桌面", True))
    else:
        log.append(("背景透明【未生效】—— 卡片外被盖住", False))

    # 卡片内应是深色（玻璃卡片），且和放窗前不同
    if card_now[0] < 80 and d_card > 30:
        log.append(("玻璃卡片【已绘制】—— 半透明深色卡片渲染成功", True))
    else:
        log.append((f"玻璃卡片【异常】—— 卡片区域平均色偏亮: "
                    f"{tuple(round(v) for v in card_now)}", False))

    log.append((f"整屏截图 -> {save_screen(os.path.join(HERE, 'probe_screen.png'))}", True))

    print("\n" + "=" * 62, flush=True)
    print("  验证结果", flush=True)
    print("=" * 62, flush=True)
    for msg, ok in log:
        print(f"  [{'√' if ok else '×'}] {msg}", flush=True)
    print("=" * 62, flush=True)
    app.quit()


def main():
    log = []
    app = QApplication(sys.argv)

    # 趁窗口没创建，先存对照样本
    base_empty = avg(grab(263, 223, 10, 10))
    base_card = avg(grab(315, 265, 220, 16))

    win = build_ui()
    win.show()

    QTimer.singleShot(1500, lambda: probe(win, log, base_empty, base_card, app))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
