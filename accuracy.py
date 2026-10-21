"""对比：给不给模型"界面布局说明"，幻觉会不会消失。

上一轮它把左上角的小地图误读成了"你暴露的区域"。
这里测试在提示词里预先讲清界面元素的位置和含义，看能否纠正。
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

BASE = ("以下是按时间顺序排列的8张游戏截图，相邻两张间隔约0.33秒，最后一张是我阵亡的瞬间。"
        "只根据界面上看得清的信息回答，不要推测你看不清的东西。"
        "最多三句话，每句不超过15个字，直接说这一波我最该改的地方。")

LAYOUT = """
【先记住这个游戏的界面布局，不要搞混】
- 左上角圆形黑框 = 小地图，它不是战斗画面，不要把它当成我所处的位置
- 左下角 = 我的血量 HP
- 右下角 = 我的弹药 AMMO
- 顶部中央 = 双方比分
- 画面正中的十字 = 准心，代表我面朝的方向
- 真正的战斗画面 = 除以上区域之外的部分
"""

for tag, prompt in [("不给布局说明", BASE), ("给了布局说明", BASE + LAYOUT)]:
    print(f"\n========== {tag} ==========")
    for run in range(2):
        content = [{"type": "text", "text": prompt}] + frames
        t0 = time.time()
        ttft = None
        text = ""
        for part in client.chat.completions.create(
                model="qwen3-vl-plus", messages=[{"role": "user", "content": content}],
                max_tokens=120, stream=True):
            if part.choices and part.choices[0].delta.content:
                if ttft is None:
                    ttft = time.time() - t0
                text += part.choices[0].delta.content
        wrong = "小地图" in text and ("暴露" in text or "黑圈" in text)
        print(f"  第{run+1}次 首字{ttft:.2f}s 说完{time.time()-t0:.2f}s "
              f"{'⚠又把小地图当位置' if wrong else '未混淆小地图'}")
        print(f"    「{text.strip()}」")
