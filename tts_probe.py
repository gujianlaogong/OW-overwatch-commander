"""语音层选型探测 v2：走百炼原生接口，试模型 + 音色 + 流式。

用法: python tts_probe.py
跑完留下 probe_*.mp3 供试听，并打印每个方案的耗时。
"""
import os
import time
import json

for k in ["HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy",
          "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)   # 这台机器代理是死的，一律直连

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = open(os.path.join(HERE, "key.txt"), encoding="utf-8").read().strip()
import dashscope
dashscope.api_key = KEY

TEXT = "你刚才站在开阔地带被爆头，下次贴右边掩体推进。"
VOICES = ["Cherry", "Serena", "Ethan", "Chelsie", "longxiaochun",
          "longxiaoxia", "longshuo"]


def save(b64s, tag):
    import base64
    raw = base64.b64decode(b64s)
    fn = f"probe_{tag}.mp3"
    with open(fn, "wb") as f:
        f.write(raw)
    return fn, len(raw)


def try_one(model, voice, stream):
    from dashscope.audio.qwen_tts import SpeechSynthesizer
    tag = f"{model.replace('/', '_')}_{voice}_{'stream' if stream else 'once'}"
    t0 = time.time()
    try:
        r = SpeechSynthesizer.call(model=model, text=TEXT, voice=voice,
                                   stream=stream)
        if stream:
            import base64
            buf = b""
            first = None
            n = 0
            for chunk in r:
                try:
                    d = chunk.output.audio.data
                except Exception:
                    continue
                if d:
                    if first is None:
                        first = time.time() - t0
                    buf += base64.b64decode(d)
                    n += 1
            if not buf:
                print(f"[×] {tag}  流式但没有音频数据")
                return
            fn, sz = save(base64.b64encode(buf).decode(), tag)
            print(f"[√] {tag}")
            print(f"    首个音频块 {first:.2f}s | 共 {n} 块 | 总 {time.time()-t0:.2f}s"
                  f" | {sz/1024:.1f}KB -> {fn}")
        else:
            r = SpeechSynthesizer.call(model=model, text=TEXT, voice=voice)
            try:
                b64s = r.output.audio.data
            except Exception:
                b64s = None
            if not b64s:
                print(f"[×] {tag}  返回里没有音频：{json.dumps(str(r)[:200])}")
                return
            fn, sz = save(b64s, tag)
            print(f"[√] {tag}  耗时 {time.time()-t0:.2f}s | {sz/1024:.1f}KB -> {fn}")
    except Exception as e:
        msg = str(e)[:160]
        print(f"[×] {tag}  {type(e).__name__}: {msg}")


def main():
    print("=" * 70)
    print("  TTS 选型探测（百炼原生接口）")
    print("=" * 70)
    # 先用一个模型探路，看看返回结构长啥样
    for v in VOICES:
        try_one("qwen3-tts-flash", v, stream=False)
    print("-" * 70)
    # 挑第一个成功的音色再测流式
    for v in VOICES[:3]:
        try_one("qwen3-tts-flash", v, stream=True)
    print("=" * 70)


if __name__ == "__main__":
    main()
