"""优化测试：找到"够快又够准"的图数与分辨率组合。

对 qwen3-vl-plus 跑多组配置，每组打印 耗时 / token / 是否读准了数字。
判定标准：回答里同时出现 30、12、3 vs 5 三个数字才算读准。
"""
import base64
import io
import os
import time

from openai import OpenAI
from PIL import Image

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3-vl-plus"

here = os.path.dirname(os.path.abspath(__file__))
key = os.path.join(here, "key.txt")
client = OpenAI(api_key=open(key, encoding="utf-8").read().strip(), base_url=BASE_URL)

src = Image.open(os.path.join(here, "test.jpg"))
cache = {}


def block(width, height):
    """按指定宽高生成 data URL，带缓存避免重复编码。"""
    k = (width, height)
    if k not in cache:
        im = src.resize((width, height), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        b = base64.b64encode(buf.getvalue()).decode()
        cache[k] = {"type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b}"}}
    return cache[k]


PROMPT = ("以下是按时间顺序排列的{n}张游戏截图，相邻两张间隔约0.33秒，"
          "最后一张是我阵亡的瞬间。请只描述你在界面上看得清的信息"
          "（血量、弹药、比分、小地图、所处地形），不要推测你看不清的东西。")

print(f"模型: {MODEL}")
print(f"{'配置':<16}{'耗时':>9}{'输入tok':>10}{'输出tok':>10}{'合计':>9}  读准")
print("-" * 72)

configs = [
    (8, 960, 540),
    (6, 960, 540),
    (4, 960, 540),
    (2, 960, 540),
    (8, 640, 360),
    (4, 640, 360),
    (8, 512, 288),
]

for n, w, h in configs:
    content = [{"type": "text", "text": PROMPT.format(n=n)}]
    content += [block(w, h) for _ in range(n)]
    try:
        t = time.time()
        r = client.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": content}],
            max_tokens=300)
        dt = time.time() - t
        txt = r.choices[0].message.content or ""
        ok = ("30" in txt) and ("12" in txt) and ("3" in txt and "5" in txt)
        u = r.usage
        print(f"{n}图@{w}x{h:<6}{dt:>8.2f}s{u.prompt_tokens:>10}{u.completion_tokens:>10}"
              f"{u.total_tokens:>9}  {'是' if ok else '否'}")
    except Exception as e:
        print(f"{n}图@{w}x{h:<6} 失败 {type(e).__name__}: {str(e)[:120]}")
