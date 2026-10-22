"""阵亡间隙复盘 · 第二层：把落盘的帧喂给视觉模型，拿到复盘结论

用法：
    python analyze.py                     # 分析 captures/ 下最新一组（默认弹出浮层卡片）
    python analyze.py --dir captures/xxx  # 分析指定一组
    python analyze.py --no-overlay        # 不弹卡片，纯控制台输出

为什么只抽 8 帧而不是全传：
    缓存了 30 秒 = 90 帧，全传既贵又慢。而阵亡前最后 10 秒才是决定性的。
    实测过：图多了并不会明显变慢（瓶颈是固定开销），但 token 是实打实变贵的。

为什么要把 layout.txt 塞进提示词：
    实测对比过：不给布局说明时，它把左上角小地图误读成"你暴露的区域"；
    给了之后不仅幻觉没了，首字延迟还从 1.85 秒降到 1.0 秒。
    layout.txt 按你玩的游戏自己改，这是整个系统里最值钱的一块。

为什么模型吐字要立刻推给界面：
    流式输出的意义就是"想到一个字就给你一个字"。如果等它说完再一次性显示，
    首字延迟那 1~2 秒就白等了。所以这里是收到一个 chunk 就推一次，
    打字机效果和模型的实际生成完全同步，一毫秒都不多等。
"""
import argparse
import base64
import os
import sys
import time
from glob import glob

from openai import OpenAI

import config as _cfg
from config import load as load_config, render_prompt

HERE = os.path.dirname(os.path.abspath(__file__))
CAP_DIR = os.path.join(HERE, "captures")
MODEL = "qwen3-vl-plus"

# 提示词的最佳整合长度和上限都放在 config.json 里（界面可调），
# 默认值由 config.DEFAULTS 提供，这里不再单独定一份，免得两处打架。

# 大陆区价格（元/百万 token），2026-10-21 核实
PRICE_IN = 1.2
PRICE_OUT = 12.0


def latest_dir():
    ds = [d for d in glob(os.path.join(CAP_DIR, "*")) if os.path.isdir(d)]
    if not ds:
        return None
    return max(ds, key=os.path.getmtime)


def load_frames(d, window=10.0, n=8):
    """读 frames.txt，取最后 window 秒，均匀抽 n 帧。"""
    idx = os.path.join(d, "frames.txt")
    if not os.path.exists(idx):
        return []
    rows = []
    for line in open(idx, encoding="utf-8").read().splitlines():
        if "," in line:
            name, rel = line.split(",")
            rows.append((name, float(rel)))
    if not rows:
        return []
    t_end = rows[-1][1]
    picked = [r for r in rows if t_end - r[1] <= window]
    if len(picked) <= n:
        return picked
    step = (len(picked) - 1) / (n - 1)
    return [picked[round(i * step)] for i in range(n)]


def img_item(path):
    b = base64.b64encode(open(path, "rb").read()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}}


def call_model(args, d, frames, prompt,
               on_chunk=None, on_first=None, on_done=None, on_usage=None):
    """发请求，边收边回调。不传回调就是纯控制台模式。
       返回 (全文, 总耗时, usage)"""
    # 关键：机器上只要设了代理变量，openai 底层就会去连那个代理端口。
    # 端口常常没在监听，于是报 WinError 10061「目标计算机积极拒绝」。
    # 实测：去掉代理直连 dashscope 是 0.29s，走代理是 0.41s —— 直连更快。
    # 所以这里一律清掉代理变量，谁设的都不管。
    for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
               "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(_k, None)

    client = OpenAI(
        api_key=open(os.path.join(HERE, "key.txt"), encoding="utf-8").read().strip(),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    content = [{"type": "text", "text": prompt}] + [
        img_item(os.path.join(d, name)) for name, _ in frames
    ]

    t0 = time.time()
    ttft = None
    text = ""
    usage = None
    try:
        for part in client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": content}],
            max_tokens=args.max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        ):
            if part.choices and part.choices[0].delta.content:
                if ttft is None:
                    ttft = time.time() - t0
                    if on_first:
                        on_first(ttft)
                ch = part.choices[0].delta.content
                text += ch
                if on_chunk:
                    on_chunk(ch)
            if getattr(part, "usage", None):
                usage = part.usage
    except Exception:
        # 回调模式（界面）由上层处理；控制台模式由 main 打印排查指引
        raise

    total = time.time() - t0
    if on_done:
        on_done(total, len(text))
    if usage and on_usage:
        on_usage(usage)
    return text, total, usage


def cost_text(usage):
    if not usage:
        return ""
    tin = getattr(usage, "prompt_tokens", 0) or 0
    tout = getattr(usage, "completion_tokens", 0) or 0
    cost = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
    return (f"token 输入 {tin} / 输出 {tout} => 本次约 {cost*100:.3f} 分钱"
            f"（一局死 10 次约 {cost*10*100:.2f} 分钱）")


def prepare(args):
    """准备好要分析的东西，返回 (目录, 帧列表, 提示词)"""
    cfg = load_config()
    d = args.dir or latest_dir()
    if not d or not os.path.isdir(d):
        print("没找到可分析的目录。先跑 coach.py 并按热键落盘。")
        return None

    frames = load_frames(d, args.window, args.n)
    if not frames:
        print(f"{d} 里没有帧。")
        return None

    span = frames[-1][1] - frames[0][1]
    gap = span / max(1, len(frames) - 1)

    layout_path = os.path.join(HERE, "layout.txt")
    layout = ""
    if os.path.exists(layout_path):
        layout = open(layout_path, encoding="utf-8").read().strip()

    prompt = render_prompt(cfg, layout=layout, n=len(frames), gap=gap)
    return d, frames, prompt


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None)
    ap.add_argument("--window", type=float, default=None,
                    help=f"只看最后几秒，默认取配置：{cfg['window']}")
    ap.add_argument("--n", type=int, default=None,
                    help=f"抽几帧，默认取配置：{cfg['frames']}")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help=f"最多说多少字，默认取配置：{cfg['max_tokens']}（再长就念不完了）")
    ap.add_argument("--no-overlay", action="store_true", help="不弹浮层卡片，只输出到控制台")
    ap.add_argument("--no-voice", action="store_true", help="不念出来")
    ap.add_argument("--voice", default=None,
                    help="音色：预置 Cherry/Serena/Ethan/Chelsie，或克隆音色 ID。"
                         "不填就按界面里配的（配置文件 → voice.txt → Cherry 依次兜底）")
    ap.add_argument("--rate", type=float, default=None,
                    help="语速，只对克隆音色有效（1.0 偏慢，1.4 开始有点赶）")
    args = ap.parse_args()

    # 命令行没给的，就用界面里配的值 —— 改完存盘，双击 run.bat 直接生效
    args.window = args.window if args.window is not None else cfg["window"]
    args.n = args.n if args.n is not None else cfg["frames"]
    args.max_tokens = (args.max_tokens if args.max_tokens is not None
                       else cfg["max_tokens"])

    want_voice = not args.no_voice
    if want_voice:
        from tts import DEFAULT_RATE, default_voice
        # 优先级：命令行 --voice　＞　界面配置　＞　voice.txt　＞　Cherry
        args.voice = args.voice or cfg.get("voice") or default_voice()
        args.rate = args.rate or cfg.get("rate") or DEFAULT_RATE

    pre = prepare(args)
    if not pre:
        return
    d, frames, prompt = pre

    print("=" * 70)
    print(f"  分析目录: {d}")
    print(f"  取最后 {args.window} 秒，抽 {len(frames)} 帧")
    print(f"  模型: {MODEL}")
    print("=" * 70)

    if args.no_overlay:
        try:
            text, total, usage = call_model(
                args, d, frames, prompt,
                on_chunk=lambda c: print(c, end="", flush=True),
                on_first=lambda t: print(f"\n  [首字 {t:.2f}s] ", end="", flush=True),
            )
        except Exception as e:
            print(f"\n  !! 调用失败：{type(e).__name__}: {str(e)[:160]}")
            print("     排查顺序：")
            print("       1) env | grep -i proxy       看是不是还有代理变量")
            print("       2) 检查 key.txt 里的 Key 有没有多余的空格或换行")
            print("       3) 确认百炼账号里 qwen3-vl-plus 是开通状态")
            return
        print(f"\n\n  ---- 说完 {total:.2f}s，共 {len(text)} 字 ----")
        print(f"  {cost_text(usage)}")
        if total > 12:
            print("\n  ⚠ 超过了阵亡等待窗口，来不及在重生前说完。考虑减少帧数或缩短 window。")
        if want_voice and text.strip():
            try:
                from tts import speak_blocking
                speak_blocking(text.strip(), voice=args.voice, rate=args.rate)
            except Exception as e:
                print(f"  !! 语音播报失败（不影响上面文字）: {type(e).__name__}: {str(e)[:140]}")
    else:
        from overlay import run_with_overlay

        speaker = None
        if want_voice:
            # 提前把合成线程拉起来：它加载 dashscope 的那几百毫秒，
            # 正好和视觉模型 2 秒的思考同时跑掉，不用额外等。
            try:
                from tts import Speaker
                speaker = Speaker(voice=args.voice, rate=args.rate)
                speaker.failed.connect(
                    lambda m: print(f"  !! 语音失败: {m}", flush=True))
                speaker.prime()
            except Exception as e:
                print(f"  !! 语音没起来（只看卡片）: {type(e).__name__}: {str(e)[:140]}")
                speaker = None

        run_with_overlay(
            call_model, args, d, frames, prompt,
            usage_cb=lambda u: print(f"  {cost_text(u)}", flush=True),
            speaker=speaker,
        )


if __name__ == "__main__":
    main()
