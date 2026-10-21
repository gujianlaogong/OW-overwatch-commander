"""百炼 API 探测：列出可用模型 + 三步递进验证（文字 / 单图 / 八图）。

用法：
  1. 把百炼的 API Key 粘贴到同目录的 key.txt（不要带引号、不要换行以外的字符）
  2. 运行： python probe.py
"""
import base64
import os
import time

from openai import OpenAI

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def load_key():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "key.txt")
    if not os.path.exists(path):
        raise SystemExit("缺少 key.txt：请在同目录新建一个，把 API Key 粘进去")
    key = open(path, encoding="utf-8").read().strip()
    if not key or key.startswith("sk-把这"):
        raise SystemExit("key.txt 里还没有填真正的 Key")
    return key


def img_block(path):
    b = base64.b64encode(open(path, "rb").read()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}}


def main():
    client = OpenAI(api_key=load_key(), base_url=BASE_URL)

    print("=" * 60)
    print("A. 你账号下可用的模型")
    print("=" * 60)
    ids = sorted(m.id for m in client.models.list().data)
    print("总数:", len(ids))
    hits = [i for i in ids if ("vl" in i or "omni" in i or "vision" in i)]
    print("--- 带视觉/多模态能力的 ---")
    for i in hits:
        print("   ", i)
    flashes = [i for i in ids if "flash" in i]
    print("--- 带 flash 的 ---")
    for i in flashes:
        print("   ", i)

    here = os.path.dirname(os.path.abspath(__file__))
    test_img = os.path.join(here, "test.jpg")

    candidates = []
    for name in ["qwen3.8-flash", "qwen3-vl-flash", "qwen3-vl-plus", "qwen-vl-max-latest"]:
        if name in ids:
            candidates.append(name)
    if not candidates:
        candidates = hits[:2] or flashes[:2]
    if not candidates:
        raise SystemExit("没找到任何可用模型，请把上面的列表发出来")

    print()
    print("=" * 60)
    print("B. 逐个模型实测（每个跑 文字 / 单图 / 八图）")
    print("=" * 60)

    for model in candidates[:3]:
        print(f"\n########## {model} ##########")
        for tag, content in [
            ("文字", "只回复两个字：收到"),
            ("单图", [{"type": "text", "text":
                       "这是游戏画面。请只回答你能在界面上看清的数字：血量、弹药、比分。"},
                      img_block(test_img)]),
            ("八图", [{"type": "text", "text":
                       "以下是按时间顺序排列的8张游戏截图，相邻两张间隔约0.33秒，"
                       "最后一张是我阵亡的瞬间。只回复你看到的界面信息，不要推测你看不清的东西。"}]
                     + [img_block(test_img) for _ in range(8)]),
        ]:
            try:
                t = time.time()
                r = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": content}],
                    max_tokens=300,
                )
                dt = time.time() - t
                u = r.usage
                print(f"[{tag}] {dt:.2f}s  输入{u.prompt_tokens} 输出{u.completion_tokens} 合计{u.total_tokens}")
                print("    ", (r.choices[0].message.content or "").replace("\n", " ")[:220])
            except Exception as e:
                print(f"[{tag}] 失败：{type(e).__name__}: {str(e)[:200]}")


if __name__ == "__main__":
    main()
