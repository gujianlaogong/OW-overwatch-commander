# -*- coding: utf-8 -*-
"""
第一步的唯一目标：验证 pywebview 能不能做出游戏 overlay 需要的三个效果
    1. 永远置顶（盖在游戏上面）
    2. 背景透明（不挡视野）
    3. 鼠标穿透（点到它身后的东西，不抢游戏鼠标）

不做视觉设计，不做后端对接。就一个窗口加一行字。
自动跑完自动关，最后打印结论。
"""
import ctypes
import ctypes.wintypes
import os
import sys
import threading
import time

import webview

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 常量
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020   # 鼠标穿透：命中测试直接跳过本窗口
WS_EX_TOOLWINDOW = 0x00000080   # 不显示在任务栏 / Alt+Tab
WS_EX_LAYERED = 0x00080000      # 分层窗口：色键抠图的前提
LWA_COLORKEY = 0x00000001       # 按"色键"抠透明：等于 key 的像素全部透过去
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.SetWindowLongW.restype = ctypes.c_long
u32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
u32.GetWindowLongW.restype = ctypes.c_long
u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
u32.WindowFromPoint.restype = ctypes.c_void_p
u32.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
u32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.RECT)]
u32.FindWindowW.restype = ctypes.c_void_p
u32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
u32.IsWindowVisible.restype = ctypes.c_bool
u32.SetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_ubyte, ctypes.c_uint]

# ---------------------------------------------------------------- 采样点
# 窗口放在 (260,220)，卡片有 18px 外边距，所以窗口内左上角那一小块是「卡片之外」
PROBE_EMPTY = (264, 224, 10, 10)     # 卡片之外 → 透明的话应该还是身后的桌面
PROBE_CARD = (300, 262, 220, 16)     # 卡片之内 → 应该看到文字

# ---------------------------------------------------------------- 界面
HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  /* 整页纯黑 = 抠图色键。任何纯黑像素都会变透明，所以卡片背景绝不能用纯黑 */
  html, body { margin:0; padding:0; background:#000000; overflow:hidden; }
  .card {
    margin: 18px;
    padding: 16px 20px;
    border-radius: 14px;
    background: #10181f;             /* 深色但不是纯黑，不会被抠掉 */
    border: 1px solid #2bd4bd55;     /* 青绿描边 */
    color: #e8f4f2;
    font: 15px/1.6 "Microsoft YaHei UI", system-ui, sans-serif;
  }
  .tag { color:#39d3bb; font-size:12px; letter-spacing:2px; }
  .txt { margin-top:8px; }
</style>
</head>
<body>
  <div class="card">
    <div class="tag">OVERLAY PROBE</div>
    <div class="txt">浮起来了就说明三件事都成立：置顶 / 透明 / 鼠标能穿过去</div>
  </div>
</body>
</html>
"""


def find_by_title(title):
    """兜底：直接按窗口标题问系统要句柄。"""
    hwnd = u32.FindWindowW(None, title)
    return int(hwnd) if hwnd else None


def get_hwnd(win, title):
    """两条路都试：先走 pywebview 的 native 属性，不行就按标题找。"""
    how = ""
    for _ in range(40):
        # 路 1：pywebview 说 Windows 上 native 是 WinForms 窗体
        native = getattr(win, "native", None)
        if native is not None:
            try:
                return int(native.Handle), "win.native.Handle"
            except Exception as e:
                how = f"native 存在但取 Handle 失败: {type(e).__name__}: {e}"
        # 路 2：按标题找
        h = find_by_title(title)
        if h:
            return h, "FindWindowW(按标题)"
        time.sleep(0.1)
    return None, how or "native 一直为 None，且按标题也没找到"


def apply_window_effects(win, title, log):
    hwnd, how = get_hwnd(win, title)
    if not hwnd:
        log.append((f"拿不到窗口句柄（{how}）", False))
        return None

    ex = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    new_ex = ex | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED
    u32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_ex)

    # 色键抠图：COLORREF 0x000000 = 纯黑。所有纯黑像素对合成器不可见
    u32.SetLayeredWindowAttributes(hwnd, 0x000000, 0, LWA_COLORKEY)

    u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    # 回读一次，确认系统真的吃下了这个设置
    back = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    log.append((f"拿到句柄 {hwnd}（来源：{how}）", True))
    log.append(("鼠标穿透 WS_EX_TRANSPARENT", bool(back & WS_EX_TRANSPARENT)))
    log.append(("不进任务栏 WS_EX_TOOLWINDOW", bool(back & WS_EX_TOOLWINDOW)))
    log.append(("分层窗口 WS_EX_LAYERED", bool(back & WS_EX_LAYERED)))

    rect = ctypes.wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rect))
    log.append((f"窗口位置 {rect.left},{rect.top} 尺寸 {rect.right-rect.left}x{rect.bottom-rect.top}", True))
    return hwnd


def verify_click_through(hwnd, log):
    """最硬的验证：问系统'这个坐标上最顶层的窗口是谁'。
       穿透成功的话，答案应该是我们身后的窗口，不是我们自己。"""
    import ctypes.wintypes as wt
    rect = wt.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rect))
    cx = (rect.left + rect.right) // 2
    cy = (rect.top + rect.bottom) // 2

    top = u32.WindowFromPoint(ctypes.wintypes.POINT(cx, cy))
    if top == hwnd:
        log.append(("鼠标穿透【未生效】—— 该坐标拿到的还是本窗口", False))
    else:
        log.append((f"鼠标穿透【生效】—— 该坐标命中的是身后窗口 (hwnd={top})", True))


def grab(x, y, w, h):
    """截取屏幕上一小块，返回 PIL 图。"""
    import mss
    from PIL import Image
    with mss.MSS() as sct:
        raw = sct.grab({"left": x, "top": y, "width": w, "height": h})
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def save_screen(path):
    """整屏存一张，肉眼直接看窗口到底长什么样。"""
    import mss
    with mss.MSS() as sct:
        sct.shot(output=path)
    return path


def avg(img):
    px = list(img.getdata())
    n = len(px)
    r = sum(p[0] for p in px) / n
    g = sum(p[1] for p in px) / n
    b = sum(p[2] for p in px) / n
    return (r, g, b)


def dist(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / 3


def verify_transparency(base_empty, base_card, log):
    """透明到底生效没有，只看像素最准：
       窗口内、卡片之外的那块地方，如果透明，就应该还是身后的桌面；
       如果不透明，就会变成一整块纯色（挡板）。"""
    empty_now = avg(grab(*PROBE_EMPTY))
    card_now = avg(grab(*PROBE_CARD))

    d_empty = dist(base_empty, empty_now)
    log.append((f"卡片外区域：放窗前 {tuple(round(v) for v in base_empty)}"
                f" → 放窗后 {tuple(round(v) for v in empty_now)}（差 {d_empty:.1f}）", True))
    log.append((f"卡片内区域：放窗前 {tuple(round(v) for v in base_card)}"
                f" → 放窗后 {tuple(round(v) for v in card_now)}"
                f"（差 {dist(base_card, card_now):.1f}）", True))

    # 判定：卡片外几乎没变 = 透明生效；变得很多 = 挡住了
    if d_empty < 12:
        log.append(("背景透明【生效】—— 卡片外透出了身后的桌面", True))
    else:
        log.append(("背景透明【未生效】—— 卡片外被整块盖住，会挡视野", False))

    # 内容得画出来才算数
    if dist(base_card, card_now) > 20:
        log.append(("卡片内容【已绘制】—— 文字和玻璃卡片确实渲染出来了", True))
    else:
        log.append(("卡片内容【没画出来】", False))


def main():
    log = []
    title = "Coach Overlay Probe"

    # 趁窗口还没创建，先把「没有 overlay 时」的样子存下来做对照
    base_empty = avg(grab(*PROBE_EMPTY))
    base_card = avg(grab(*PROBE_CARD))
    win = webview.create_window(
        title,
        html=HTML,
        width=560, height=160,
        x=260, y=220,
        frameless=True,
        transparent=False,          # pywebview 的透明在 Windows 上是半成品，弃用
        background_color="#000000", # 窗体底色 = 色键，被整块抠掉
        on_top=True,
        easy_drag=False,
    )

    def on_started():
        hwnd = None
        try:
            hwnd = apply_window_effects(win, title, log)

            # pywebview 透明模式的坑：窗口先 Show 再 Hide，指望 Navigating 事件弹回来，
            # 但这个事件不一定来。藏着就手动 show。
            time.sleep(0.8)
            if hwnd and not u32.IsWindowVisible(hwnd):
                log.append(("窗口处于隐藏状态（pywebview 透明模式已知行为）→ 手动 show()", True))
                win.show()
                time.sleep(1.0)
            else:
                log.append(("窗口可见", True))

            time.sleep(0.5)

            # 让页面自己报告状态，比隔着屏幕猜准得多
            try:
                js = {
                    "ready": win.evaluate_js("document.readyState"),
                    "bodyBg": win.evaluate_js("getComputedStyle(document.body).backgroundColor"),
                    "cardBg": win.evaluate_js("getComputedStyle(document.querySelector('.card')).backgroundColor"),
                    "textLen": win.evaluate_js("document.body.innerText.length"),
                }
                log.append((f"页面自报: readyState={js['ready']} body背景={js['bodyBg']} "
                            f"卡片背景={js['cardBg']} 文字长度={js['textLen']}", True))
            except Exception as e:
                log.append((f"evaluate_js 失败: {type(e).__name__}: {e}", False))

            if hwnd:
                verify_click_through(hwnd, log)
            verify_transparency(base_empty, base_card, log)
            shot = save_screen(os.path.join(HERE, "probe_screen.png"))
            log.append((f"整屏截图已存 -> {shot}", True))
        except Exception as e:
            import traceback
            log.append((f"验证过程抛异常: {type(e).__name__}: {e}", False))
            traceback.print_exc()

        print("\n" + "=" * 62, flush=True)
        print("  验证结果", flush=True)
        print("=" * 62, flush=True)
        for msg, ok in log:
            print(f"  [{'√' if ok else '×'}] {msg}", flush=True)
        print("=" * 62, flush=True)
        print("  4 秒后自动关闭窗口…", flush=True)
        time.sleep(4)
        try:
            win.destroy()
        except Exception:
            pass
        sys.stdout.flush()
        os._exit(0)

    webview.start(on_started, gui="edgechromium")


if __name__ == "__main__":
    main()
