"""最终验证：用 8 张真实不同的帧（血量 100→0 的一波交火），测真实耗时与理解力。

同时试一下 qwen3-vl-flash —— 它的单价只有 plus 的 15%，但你的模型列表里没显示。
"""
import base64
import io
import os
import time

from openai import OpenAI
from PIL import Image, ImageDraw

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
here = os.path.dirname(os.path.abspath(__file__))
client = OpenAI(api_key=open(os.path.join(here, "key.txt"), encoding="utf-8").read().strip(),
                base_url=BASE_URL)

HP = [100, 88, 71, 55, 40, 26, 12, 0]
AMMO = [30, 28, 24, 21, 21, 15, 9, 9]


def make_frame(i):
    W, H = 960, 540
    img = Image.new("RGB", (W, H), (58, 66, 52))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 240], fill=(96, 122, 140))
    d.rectangle([0, 240, W, H], fill=(74, 82, 66))
    shift = i * 22
    d.rectangle([120 - shift, 180, 260 - shift, 300], fill=(92, 88, 80))
    d.rectangle([600 + shift, 150, 760 + shift, 310], fill=(84, 80, 74))
    d.rectangle([380, 330, 520, 430], fill=(110, 104, 92))
    cx = W // 2 + (i * 9 - 30)
    d.line([cx - 14, H // 2, cx - 4, H // 2], fill=(255, 255, 255), width=2)
    d.line([cx + 4, H // 2, cx + 14, H // 2], fill=(255, 255, 255), width=2)
    d.rectangle([40, 460, 240, 500], fill=(30, 30, 30))
    d.text((52, 474), f"HP  {HP[i]} / 100",
           fill=(230, 80, 80) if HP[i] < 40 else (120, 230, 120))
    d.rectangle([720, 460, 920, 500], fill=(30, 30, 30))
    d.text((732, 474), f"AMMO  {AMMO[i]} / 90", fill=(240, 240, 240))
    d.rectangle([380, 20, 580, 56], fill=(30, 30, 30))
    d.text((430, 32), "3  vs  5", fill=(255, 255, 255))
    d.rectangle([40, 40, 200, 200], fill=(20, 20, 20))
    d.ellipse([40, 40, 200, 200], outline=(200, 200, 200), width=2)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return {"type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,"
                                + base64.b64encode(buf.getvalue()).decode()}}


frames = [make_frame(i) for i in range(8)]
print("已生成 8 张不同的帧：HP", HP[0], "→", HP[-1], "  AMMO", AMMO[0], "→", AMMO[-1])

PROMPT = ("以下是按时间顺序排列的8张游戏截图，相邻两张间隔约0.33秒，最后一张是我阵亡的瞬间。"
          "只根据界面上看得清的信息回答，不要推测你看不清的东西。"
          "最多三句话，每句不超过15个字，直接说这一波我最该改的地方。")

for model in ["qwen3-vl-plus", "qwen3-vl-flash"]:
    print(f"\n########## {model} ##########")
    content = [{"type": "text", "text": PROMPT}] + frames
    try:
        t0 = time.time()
        ttft = None
        text = ""
        for part in client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": content}],
                max_tokens=120, stream=True):
            if part.choices and part.choices[0].delta.content:
                if ttft is None:
                    ttft = time.time() - t0
                text += part.choices[0].delta.content
        print(f"首字 {ttft:.2f}s  说完 {time.time()-t0:.2f}s  共{len(text)}字")
        print(f"「{text.strip()}」")
    except Exception as e:
        print(f"不可用：{type(e).__name__}: {str(e)[:160]}")
