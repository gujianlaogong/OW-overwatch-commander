"""阵亡间隙复盘 · 第一层：后台滚动缓存 + 热键触发落盘

这是整个系统的地基。它只干一件事：
    在后台以 3 FPS 持续缓存最近 30 秒的画面，你一按热键就把这 30 秒存成一组 JPEG。

为什么必须在"死之前"就缓存：
    你死了的那一刻，画面已经变了（死亡镜头、灰屏）。
    所以绝不能"死了再截图"，必须活着的时候就一直存着。

设计要点：
    - 缓存里存的是【压好的 JPEG 字节】，不是原始像素。
      960x540 原始像素 1.5MB/帧 x 90 帧 = 138MB；压成 JPEG 约 60KB/帧，90 帧才 5MB。
      而且落盘时直接写字节，不用再编码，落盘特别快。
    - 按热键后【再多抓 2 秒】才落盘 —— 死亡后的画面里有击杀者信息、伤害来源，很值钱。
    - 抓帧用 DXGI 桌面复制，不碰游戏进程，和 OBS/录屏软件用的是同一套机制。

用法：
    python coach.py                  # 默认 F8 触发，3 FPS，缓存 30 秒
    python coach.py --key f9         # 换个热键
    python coach.py --backend mss    # dxcam 抓不到时换 mss

跑起来之后：
    开游戏（建议无边框窗口模式）→ 死后按 F8 → 看控制台打印 → 去 captures/ 目录看落盘的图
"""
import argparse
import ctypes
import os
import queue
import sys
import threading
import time
from collections import deque
from datetime import datetime

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "captures")

TARGET_W, TARGET_H = 960, 540
JPEG_QUALITY = 85


# ---------------------------------------------------------------- 触发
VK = {"space": 0x20, "enter": 0x0D, "tab": 0x09,
      "xbutton1": 0x05, "xbutton2": 0x06}  # 后两个是鼠标侧键，FPS 里很好用
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    VK[_c] = 0x41 + _i
for _i in range(10):
    VK[str(_i)] = 0x30 + _i
for _i in range(1, 25):
    VK[f"f{_i}"] = 0x6F + _i


class KeyPoller:
    """轮询按键状态，不装系统钩子。

    为什么不用 keyboard 库的全局钩子（本机实测过的理由）：
      1. 实测注册成功但回调不触发 —— 这台办公机有终端管控，钩子这条路不可靠
      2. 装钩子 = 进入系统钩子链，对游戏反作弊来说比"只读状态"显眼得多
      3. 轮询是纯只读：每 20ms 问一次"这个键按着吗"，CPU 占用基本为零
    """

    def __init__(self, key, on_fire):
        self.vk = VK[key.lower()]
        self.on_fire = on_fire
        self.held = False
        self.stop_flag = threading.Event()
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()

    def _loop(self):
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        while not self.stop_flag.is_set():
            down = bool(u32.GetAsyncKeyState(self.vk) & 0x8000)
            if down and not self.held:      # 只在"刚按下"那一刻触发一次
                self.held = True
                self.on_fire()
            elif not down:
                self.held = False
            time.sleep(0.02)

    def stop(self):
        self.stop_flag.set()
        self.th.join(timeout=1)


# ---------------------------------------------------------------- 抓帧后端
class Grabber:
    """统一两种抓帧后端：dxcam(DXGI桌面复制，快) 优先，mss(GDI，通用) 兜底。"""

    def __init__(self, backend="auto", monitor=0):
        self.backend = None
        self.cam = None
        self.sct = None
        self.mon = None
        order = ["dxcam", "mss"] if backend == "auto" else [backend]
        for name in order:
            try:
                if name == "dxcam":
                    import dxcam

                    cam = dxcam.create(output_color="RGB")
                    if cam is None:
                        continue
                    cam.start(target_fps=30, video_mode=True)
                    time.sleep(0.2)
                    if cam.get_latest_frame() is None:
                        cam.stop()
                        continue
                    self.cam, self.backend = cam, "dxcam"
                else:
                    from mss import mss as MSS

                    sct = MSS()
                    self.sct, self.mon = sct, sct.monitors[1 + monitor]
                    self.backend = "mss"
                print(f"[抓帧] 使用后端: {self.backend}")
                return
            except Exception as e:
                print(f"[抓帧] {name} 启动失败: {type(e).__name__}: {e}")
        raise RuntimeError("没有任何抓帧后端可用。先跑 cap_probe.py 看看。")

    def grab(self):
        """返回 RGB numpy 数组；失败返回 None。"""
        try:
            if self.backend == "dxcam":
                return self.cam.get_latest_frame()
            raw = self.sct.grab(self.mon)
            return np.asarray(raw)[:, :, :3]
        except Exception:
            return None

    def close(self):
        try:
            if self.backend == "dxcam" and self.cam:
                self.cam.stop()
            elif self.sct:
                self.sct.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 滚动缓存
class RollingBuffer:
    def __init__(self, grabber, fps=3, seconds=30):
        self.g = grabber
        self.interval = 1.0 / fps
        self.frames = deque(maxlen=int(fps * seconds))  # 满了自动丢最老的
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.seq = 0
        self.last_cost_ms = 0.0
        self.dropped = 0
        self.bytes_total = 0
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()

    def _encode(self, arr):
        """原始像素 -> 缩放到 960x540 -> JPEG 字节"""
        img = Image.fromarray(arr).resize((TARGET_W, TARGET_H), Image.BILINEAR)
        import io

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        return buf.getvalue()

    def _loop(self):
        while not self.stop_flag.is_set():
            t = time.perf_counter()
            arr = self.g.grab()
            if arr is None:
                self.dropped += 1
            else:
                data = self._encode(arr)
                with self.lock:
                    old = self.frames[0][1] if self.frames and len(self.frames) == self.frames.maxlen else None
                    self.frames.append((time.time(), data, self.seq))
                    self.bytes_total += len(data)
                    if old is not None:
                        self.bytes_total -= len(old)
                    self.seq += 1
                self.last_cost_ms = (time.perf_counter() - t) * 1000
            time.sleep(max(0.0, self.interval - (time.perf_counter() - t)))

    def snapshot(self):
        with self.lock:
            return list(self.frames)

    def stats(self):
        with self.lock:
            return dict(n=len(self.frames), mb=self.bytes_total / 1024 / 1024,
                        cap=self.frames.maxlen, cost=self.last_cost_ms,
                        dropped=self.dropped, seq=self.seq)

    def stop(self):
        self.stop_flag.set()
        self.th.join(timeout=2)


# ---------------------------------------------------------------- 落盘
def dump(frames, tag=""):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = os.path.join(OUT_DIR, ts)
    os.makedirs(d, exist_ok=True)
    t0 = frames[0][0] if frames else 0.0
    lines = []
    for i, (t, data, seq) in enumerate(frames):
        name = f"f{i:03d}.jpg"
        with open(os.path.join(d, name), "wb") as f:
            f.write(data)
        lines.append(f"{name},{t - t0:.3f}")  # 相对第一帧的秒数
    with open(os.path.join(d, "frames.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(d, "meta.txt"), "w", encoding="utf-8") as f:
        f.write(f"时间 {ts}\n帧数 {len(frames)}\n标签 {tag}\n")
        if frames:
            f.write(f"跨度 {frames[-1][0] - t0:.1f} 秒\n")
    return d, len(frames)


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="f8", help="触发热键，默认 f8")
    ap.add_argument("--fps", type=float, default=3.0, help="抓帧频率，默认 3")
    ap.add_argument("--seconds", type=float, default=30.0, help="缓存时长，默认 30 秒")
    ap.add_argument("--backend", default="auto", choices=["auto", "dxcam", "mss"])
    ap.add_argument("--post", type=float, default=2.0, help="按键后继续抓几秒，默认 2")
    ap.add_argument("--analyze", action="store_true", help="落盘后自动调 analyze.py 出复盘")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    g = Grabber(args.backend)
    buf = RollingBuffer(g, args.fps, args.seconds)

    fire = queue.Queue()
    key = args.key.lower()
    if key not in VK:
        print(f"[热键] 不认识的键「{args.key}」。可用：f1~f24 / a~z / 0~9 / space / "
              f"enter / xbutton1 / xbutton2（后两个是鼠标侧键）")
        return
    poller = KeyPoller(key, lambda: fire.put(time.time()))

    print("=" * 70)
    print(f"  后台缓存已启动：{args.fps} FPS / 最近 {args.seconds} 秒 / 960x540 JPEG")
    print(f"  触发：按 {key.upper()}（按下后会再抓 {args.post} 秒才落盘）")
    print(f"  输出目录：{OUT_DIR}")
    print(f"  退出：Ctrl+C")
    print("=" * 70)
    print("  现在就去开游戏。两点提醒：")
    print("  1) 游戏显示模式请设成【无边框窗口】—— 独占全屏很可能抓到纯黑画面")
    print("  2) 先在桌面按一次键，确认控制台有反应，再进游戏\n")

    last_fire = 0.0
    try:
        while True:
            # 状态行（每 0.5 秒刷新一次）
            s = buf.stats()
            print(f"\r  缓存 {s['n']}/{s['cap']} 帧 | 占内存 {s['mb']:.1f} MB | "
                  f"单帧耗时 {s['cost']:.0f} ms | 丢帧 {s['dropped']}      ",
                  end="", flush=True)
            try:
                tf = fire.get(timeout=0.5)
            except queue.Empty:
                continue
            if tf - last_fire < 3.0:      # 冷却，防手抖连发
                continue
            last_fire = tf

            print("\n  >> 收到触发，继续抓 %.1f 秒…" % args.post, flush=True)
            time.sleep(args.post)  # 让死亡后的画面也进来
            frames = buf.snapshot()
            if not frames:
                print("  !! 缓冲区是空的，一帧都没抓到")
                continue
            t0 = time.time()
            d, n = dump(frames)
            print(f"  >> 已落盘 {n} 帧 -> {d}   耗时 {(time.time()-t0)*1000:.0f} ms",
                  flush=True)
            if args.analyze:
                import subprocess

                print()
                subprocess.run([sys.executable, os.path.join(HERE, "analyze.py"),
                                "--dir", d], cwd=HERE)
    except KeyboardInterrupt:
        print("\n\n  退出中…")
    finally:
        poller.stop()
        buf.stop()
        g.close()


if __name__ == "__main__":
    main()
