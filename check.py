"""装完先跑一次这个：python check.py

体检表。就回答一个问题：「我装对了吗？」

- 不花钱（默认完全不联网）
- 不弹窗（Qt 只创建实例，不显示）
- 大约 3 秒跑完

    python check.py             基础体检
    python check.py --online    额外验证 API Key 和联网（免费，不调模型，不花钱）
"""
import os
import sys
import time
import traceback
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ONLINE = "--online" in sys.argv
ROWS = []


def pad(s, width):
    """中文占两格，按眼睛看到的宽度补空格，否则表格会歪。"""
    shown = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)
    return s + " " * max(1, width - shown)


def check(name, fn, hint=""):
    """fn 返回 (状态, 说明)，状态是 ok / warn / fail。"""
    try:
        state, msg = fn()
    except Exception as e:
        state = "fail"
        msg = f"{type(e).__name__}: {e}"
        last = traceback.format_exc().strip().splitlines()[-1]
        if last and last not in msg:
            msg += f"  ({last.strip()})"
    if state == "fail" and hint:
        msg += f"\n          -> {hint}"
    ROWS.append((name, state))
    tag = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[state]
    print(f"[{tag}] {pad(name, 16)}{msg}")
    return state


def imp(mod, attr=None, hint=""):
    """生成一个「能不能 import」的检查函数。"""

    def f():
        m = __import__(mod, fromlist=[attr] if attr else [])
        if attr:
            getattr(m, attr)
        return "ok", getattr(m, "__version__", "") or "已装"

    f.hint = hint
    return f


# ---------------------------------------------------------------- 各项检查
def _python():
    v = sys.version_info
    s = f"{v.major}.{v.minor}.{v.micro}"
    return ("ok", s) if v >= (3, 10) else ("fail", f"{s}  需要 3.10 以上")


def _grab():
    """最关键的一项：主程序到底会用哪个抓帧后端、快不快、抓到的是不是黑屏。"""
    import numpy as np
    from coach import Grabber

    g = Grabber("auto")
    backend = g.backend
    ts, frame = [], None
    try:
        for _ in range(5):
            t = time.perf_counter()
            frame = g.grab()
            ts.append((time.perf_counter() - t) * 1000)
    finally:
        g.close()

    med = sorted(ts)[len(ts) // 2]
    state = "ok" if backend == "dxcam" else "warn"
    msg = f"{backend}   中位 {med:.0f} ms/帧"

    if frame is None:
        return "fail", f"{backend} 抓不到帧"
    a = np.asarray(frame)
    mean, std = float(a.mean()), float(a.std())
    msg += f"   画面 mean={mean:.0f} std={std:.0f}"
    if mean < 3.0 and std < 3.0:
        state = "warn"
        msg += "  !! 全黑"
    elif std < 1.0:
        state = "warn"
        msg += "  !! 纯色画面"
    return state, msg


def _key():
    p = os.path.join(HERE, "key.txt")
    if not os.path.exists(p):
        return "fail", "没有 key.txt"
    s = open(p, encoding="utf-8").read().strip()
    if not s:
        return "fail", "key.txt 是空的"
    return "ok", f"已填（{len(s)} 个字符，内容不打出来）"


def _layout():
    p = os.path.join(HERE, "layout.txt")
    if not os.path.exists(p):
        return "warn", "没有 layout.txt，模型会自己瞎猜界面"
    n = len(open(p, encoding="utf-8").read().strip())
    return ("ok", f"{n} 字") if n else ("warn", "layout.txt 是空的")


def _outdir():
    p = os.path.join(HERE, "captures")
    try:
        os.makedirs(p, exist_ok=True)
        probe = os.path.join(p, ".write_test")
        open(probe, "w").write("1")
        os.remove(probe)
    except Exception as e:
        return "fail", f"写不进去：{e}"
    return "ok", "captures/ 可写"


def _online():
    """只问「这个 Key 能用吗」，不调模型，所以不花钱。"""
    key = open(os.path.join(HERE, "key.txt"), encoding="utf-8").read().strip()
    # 代理变量会让底层去连一个没监听的端口（WinError 10061），一律清掉
    for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
               "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(_k, None)
    from openai import OpenAI

    client = OpenAI(
        api_key=key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout=20.0,
    )
    t = time.perf_counter()
    models = client.models.list()
    ms = (time.perf_counter() - t) * 1000
    n = len(getattr(models, "data", []) or [])
    return "ok", f"Key 有效，可见 {n} 个模型，耗时 {ms:.0f} ms"


# ---------------------------------------------------------------- 跑
def main():
    print("=" * 70)
    print("  装完体检" + ("（含联网验证）" if ONLINE else ""))
    print("=" * 70)

    check("Python", _python, "装 Python 3.10 以上：https://www.python.org/downloads/")

    print("\n--- 依赖 ---")
    for name, mod, attr, hint in [
        ("numpy", "numpy", None, "pip install numpy"),
        ("opencv", "cv2", None,
         "★ dxcam 的硬依赖。缺了它 dxcam 会静默降级到 mss，慢 3 倍。"
         " 装：pip install opencv-python"),
        ("pillow", "PIL", None, "pip install pillow"),
        ("dxcam", "dxcam", None, "pip install dxcam"),
        ("mss", "mss", None, "pip install mss"),
        ("openai", "openai", None, "pip install openai"),
        ("dashscope", "dashscope", None, "pip install dashscope（语音层要用）"),
    ]:
        check(name, imp(mod, attr), hint)

    print("\n--- 界面与音频 ---")
    check("Qt 界面", imp("PySide6.QtWidgets", "QApplication"), "pip install PySide6")
    check("Qt 音频", imp("PySide6.QtMultimedia", "QAudioSink"),
          "pip install PySide6（音频在 PySide6_Addons 里，装主包会自带）")

    print("\n--- 关键项 ---")
    check("抓帧后端", _grab,
          "想要 dxcam 却掉到 mss？九成是缺 opencv-python，装上重启这一项就绿了")
    check("API Key", _key,
          "去 bailian.console.aliyun.com 建一个 Key，粘进 key.txt，一行，别加引号")
    check("布局文件", _layout,
          "README 里说它是整个项目最值钱的文件，换游戏就改它")
    check("输出目录", _outdir, "当前用户对这个文件夹没写权限，换个地方放项目")

    if ONLINE:
        print("\n--- 联网 ---")
        check("API 连通", _online,
              "Key 不对 / 过期，或者网络不通。代理变量会被自动清掉，不用自己管")

    # ------------------------------------------------------------ 总结
    n_fail = sum(1 for _, s in ROWS if s == "fail")
    n_warn = sum(1 for _, s in ROWS if s == "warn")
    print("\n" + "=" * 70)
    if n_fail:
        print(f"  有 {n_fail} 项没过（{'另有 ' + str(n_warn) + ' 项警告' if n_warn else ''}）。"
              f"照上面 -> 后面的提示修，修完再跑一次这个脚本。")
    elif n_warn:
        print(f"  能用，但有 {n_warn} 项警告，建议看一下上面带 !! 或 WARN 的行。")
    else:
        print("  全绿。双击 run.bat 就能用了。")
        print("  进游戏前先在桌面按一次 F8，确认控制台有反应。")
    if not ONLINE:
        print("  （这次没验证 API Key。跑 python check.py --online 可以免费验一次）")
    print("=" * 70)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
