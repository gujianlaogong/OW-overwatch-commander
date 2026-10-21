"""抓帧库探测（第二版，修好调用姿势）
目标：找出这台机器上真正能用、够快、不黑屏的抓帧方案。

用法：
    python cap_probe.py            # 桌面状态下跑
    python cap_probe.py game       # 开着游戏跑（验证会不会黑屏）

全屏独占模式的游戏，普通截图经常会抓到纯黑。
WGC(Windows Graphics Capture) 是唯一能可靠抓到独占全屏的方案，所以必须留着当保底。
"""
import sys
import time
import traceback

import numpy as np
from PIL import Image

MODE = "game" if len(sys.argv) > 1 and sys.argv[1] == "game" else "desktop"
N_BENCH = 30

print("=" * 78)
print(f"抓帧库探测   模式={MODE}   目标缩放 960x540")
print("=" * 78)


def report(name, ok, msg):
    print(f"[{'OK  ' if ok else 'FAIL'}] {name:<16} {msg}")


def check_frame(img):
    if img is None:
        return "帧为 None"
    arr = np.asarray(img)
    if arr.size == 0:
        return "帧尺寸为 0"
    mean, std = float(arr.mean()), float(arr.std())
    if mean < 3.0 and std < 3.0:
        return f"!! 疑似全黑 mean={mean:.2f} std={std:.2f}"
    if std < 1.0:
        return f"!! 纯色画面 mean={mean:.2f} std={std:.2f}"
    return f"有内容 mean={mean:.1f} std={std:.1f} shape={arr.shape}"


def bench(grab, n=N_BENCH):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        f = grab()
        ts.append((time.perf_counter() - t) * 1000)
        if f is None:
            return None, None, "抓帧返回 None"
    ts.sort()
    avg = sum(ts) / len(ts)
    return ts[len(ts) // 2], avg, f"中位{ts[len(ts)//2]:.1f}ms 平均{avg:.1f}ms (~{1000/avg:.0f} FPS)"


def save(arr, name):
    try:
        a = np.asarray(arr)
        if a.ndim == 3 and a.shape[2] == 4:
            a = a[:, :, :3]
        Image.fromarray(a).resize((960, 540)).save(name)
        return f"已存 {name}"
    except Exception as e:
        return f"存图失败 {e}"


results = {}

# ---------------- 1. mss ----------------
print("\n--- 1. mss (GDI BitBlt，最通用的老办法) ---")
try:
    from mss import mss as MSS

    with MSS() as sct:
        mon = sct.monitors[1]
        grab = lambda: np.asarray(sct.grab(mon))[:, :, :3]
        f0 = grab()
        report("mss 首帧", True, check_frame(f0))
        med, avg, msg = bench(grab)
        report("mss 速度", med is not None, msg)
        print("        ", save(f0, "probe_mss.png"))
        if med:
            results["mss"] = (med, avg)
except Exception:
    report("mss", False, traceback.format_exc().strip().splitlines()[-1])

# ---------------- 2. dxcam (video_mode) ----------------
print("\n--- 2. dxcam (DXGI 桌面复制，video_mode) ---")
try:
    import dxcam

    cam = dxcam.create(output_color="RGB")
    if cam is None:
        report("dxcam", False, "create() 返回 None")
    else:
        cam.start(target_fps=60, video_mode=True)
        time.sleep(0.3)
        f0 = cam.get_latest_frame()
        report("dxcam 首帧", f0 is not None, check_frame(f0))
        if f0 is not None:
            med, avg, msg = bench(lambda: cam.get_latest_frame())
            report("dxcam 速度", med is not None, msg)
            print("        ", save(f0, "probe_dxcam.png"))
            if med:
                results["dxcam"] = (med, avg)
        cam.stop()
except Exception:
    report("dxcam", False, traceback.format_exc().strip().splitlines()[-1])

# ---------------- 3. WGC (windows-capture) ----------------
print("\n--- 3. WGC (Windows Graphics Capture，能抓独占全屏) ---")
try:
    from windows_capture import WindowsCapture, Frame

    box = {"f": None}

    # 注意：monitor_index 从 1 开始，传 0 会报错
    capture = WindowsCapture(cursor_capture=False, draw_border=False, monitor_index=1)

    @capture.event
    def on_frame_arrived(frame: Frame, capture_control):
        box["f"] = np.asarray(frame)
        capture_control.stop()

    @capture.event
    def on_closed():
        pass

    t = time.perf_counter()
    capture.start()
    first_delay = (time.perf_counter() - t) * 1000
    if box["f"] is not None:
        report("WGC 首帧", True, f"{check_frame(box['f'])}  启动到首帧 {first_delay:.0f}ms")
        print("        ", save(box["f"], "probe_wgc.png"))
    else:
        report("WGC 首帧", False, "未收到帧")
except Exception:
    report("WGC", False, traceback.format_exc().strip().splitlines()[-1])

# ---------------- 4. keyboard ----------------
print("\n--- 4. keyboard (全局热键) ---")
try:
    import keyboard

    report("keyboard", True, "库已装。全局热键要开游戏后单独测（cap_hotkey_test.py）")
except Exception:
    report("keyboard", False, traceback.format_exc().strip().splitlines()[-1])

print("\n" + "=" * 78)
if results:
    print("可用方案（按速度排）：")
    for k, v in sorted(results.items(), key=lambda x: x[1][1]):
        print(f"   {k:<12} 中位 {v[0]:6.1f} ms   平均 {v[1]:6.1f} ms   (~{1000/v[1]:.0f} FPS)")
else:
    print("!! 一个都没跑通")
print("=" * 78)
print("我们的目标只有 3 FPS（每 333ms 一帧）。")
print("所以速度根本不是问题 —— 唯一的真问题是【开游戏后会不会黑屏】。")
if MODE == "desktop":
    print("\n现在跑的是桌面。请开着游戏再跑一次：")
    print("    python cap_probe.py game")
    print("然后看每个方案的 check 结果有没有出现『!! 疑似全黑』。")
else:
    print("\n如果出现『!! 疑似全黑』，那个方案就不能用于独占全屏，")
    print("改用 WGC，或者把游戏显示模式改成『无边框窗口』。")
