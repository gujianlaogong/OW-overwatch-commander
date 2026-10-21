"""端到端自测：不开游戏、不按热键，把整条链路走一遍。

验证四件事：
  1. 抓帧能不能工作，单帧耗时多少
  2. 滚动缓存会不会无限涨内存（这是最容易写错的地方）
  3. 落盘快不快（目标 < 1 秒）
  4. analyze.py 能不能跑通、出什么结论、花多少钱

用法：
    python selftest.py
现在没开游戏，抓到的是桌面，所以模型说的话会是"桌面"相关的 —— 这没关系，
我们要验证的是【链路通不通】和【耗时/内存/成本】，不是结论对不对。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from coach import Grabber, RollingBuffer, dump  # noqa: E402

print("=" * 70)
print("  端到端自测（抓帧 -> 缓存 -> 落盘 -> 分析）")
print("=" * 70)

g = Grabber("auto")
buf = RollingBuffer(g, fps=3, seconds=30)

print("\n[1] 抓帧 + 缓存，跑 8 秒（每 2 秒报一次）…")
for i in range(4):
    time.sleep(2)
    s = buf.stats()
    print(f"    {2*(i+1):>2}s  缓存 {s['n']}/{s['cap']} 帧  内存 {s['mb']:.2f} MB  "
          f"单帧 {s['cost']:.0f} ms  丢帧 {s['dropped']}")

s = buf.stats()
print(f"\n[2] 内存检查：{s['mb']:.2f} MB（上限应稳定，不随时间涨）")
print(f"    单帧耗时 {s['cost']:.0f} ms（目标 3 FPS = 每 333ms 一帧，必须远小于它）")

print("\n[3] 模拟按下热键 -> 落盘…")
frames = buf.snapshot()
t0 = time.time()
d, n = dump(frames, tag="selftest")
cost = (time.time() - t0) * 1000
print(f"    落盘 {n} 帧 -> {d}")
print(f"    落盘耗时 {cost:.0f} ms  {'OK (<1秒)' if cost < 1000 else '!! 太慢'}")

buf.stop()
g.close()

print("\n[4] 调 analyze.py 分析这组帧…\n")
subprocess.run([sys.executable, os.path.join(HERE, "analyze.py"), "--dir", d], cwd=HERE)

print("\n" + "=" * 70)
print("  自测结束。检查上面四项：")
print("   - 缓存帧数在涨、内存稳定在个位数 MB  -> 缓存层 OK")
print("   - 落盘 < 1 秒                        -> 触发层 OK")
print("   - analyze 有输出、有 token 统计      -> 分析层 OK")
print("  下一步：开游戏（无边框窗口模式）跑 python coach.py --analyze")
print("=" * 70)
