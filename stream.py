"""测真正的体验指标：多久吐出第一个字（TTFT），以及说完要多久。

等待窗口只有十几秒，所以关键不是"总共多久"，而是"多久能开始念给你听"。
"""
import base64
import os
import time

from openai import OpenAI

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
here = os.path.dirname(os.path.abspath(__file__))
client = OpenAI(api_key=open(os.path.join(here, "key.txt"), encoding="utf-8").read().strip(),
                base_url=BASE_URL)

b = base64.b64encode(open(os.path.join(here, "test.jpg"), "rb").read()).decode()
IMG = {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}}

SHORT = ("以下是按时间顺序排列的8张游戏截图，相邻间隔约0.33秒，最后一张是我阵亡瞬间。"
         "只根据界面上看得清的信息回答，不要推测。最多三句话，每句不超过15个字。")

LONG = ("以下是按时间顺序排列的8张游戏截图，相邻间隔约0.33秒，最后一张是我阵亡瞬间。"
        "请分析我这一波的问题并给出下一回合建议。")


def run_stream(model, prompt, tag):
    content = [{"type": "text", "text": prompt}] + [IMG] * 8
    t0 = time.time()
    ttft = None
    chunks = 0
    text = ""
    try:
        for part in client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": content}],
                max_tokens=120, stream=True):
            if part.choices and part.choices[0].delta.content:
                if ttft is None:
                    ttft = time.time() - t0
                chunks += 1
                text += part.choices[0].delta.content
    except Exception as e:
        print(f"[{tag}] 失败 {type(e).__name__}: {str(e)[:150]}")
        return
    total = time.time() - t0
    print(f"[{tag}] 首字 {ttft:.2f}s  说完 {total:.2f}s  共{len(text)}字 {chunks}块")
    print(f"    「{text.strip()}」")


print("== qwen3-vl-plus ==")
run_stream("qwen3-vl-plus", SHORT, "短提示 流式")
run_stream("qwen3-vl-plus", LONG, "长提示 流式")

print("\n== qwen3.8-omni-flash（更新的全模态）==")
run_stream("qwen3.8-omni-flash", SHORT, "短提示 流式")

print("\n== qwen-vl-ocr（专用读数字，看它多快）==")
run_stream("qwen-vl-ocr", "读出这张游戏截图里所有界面上的数字", "OCR 流式")
