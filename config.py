"""全项目唯一的配置来源。

以前这些东西是散的：你的名字写死在 analyze.py，卡片抬头写死在 overlay.py，
音色藏在 voice.txt 里。想改个称呼得动手改代码。

现在统一收 CONFIG_PATH（config.json）里：
  界面(settings.py) 负责写  ->  config.json  ->  各模块负责读

没有 config.json 也能跑 —— 直接拿下面的 DEFAULTS，行为和改版之前一模一样。
所以就算这个文件被删了、写坏了，程序也不会崩，只是退回出厂设定。
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")

# 提示词模板。四个称呼都用花括号占位，界面上改完存盘就生效。
# {layout} 是给 layout.txt 留的插口 —— 那份界面说明单独存，因为它最长也最常改。
PROMPT_TEMPLATE = """你是{coach_name}，正在给{user_name}做战后复盘。
称呼对方用「{user_title}」，提到你自己时用「{coach_self}」。

以下是按时间顺序排列的 {n} 张游戏截图，相邻两张间隔约 {gap} 秒，
最后一张是我阵亡的瞬间或刚阵亡之后。

{layout}

现在告诉我：这一波我最该改的地方是什么。
只讲最该改的一条，控制在 30 字以内，说完立刻停 ——
当然，如果我有做的不错的地方你也要夸我，也要控制字数。
这段话是要念出来听的，长了来不及在重生前听完。"""

DEFAULTS = {
    # ---- 称呼 ----
    "user_name": "古见老公",     # 我叫什么
    "user_title": "指挥官",      # 教练叫我什么
    "coach_name": "D.VA",        # 教练叫什么
    "coach_self": "本机",        # 教练自称什么

    # ---- 提示词 ----
    "prompt": PROMPT_TEMPLATE,
    "max_tokens": 60,            # 结论最长多少字；越长越念不完

    # ---- 看多长一段画面 ----
    "window": 10.0,              # 分析最后几秒
    "frames": 8,                 # 抽几帧喂给模型

    # ---- 声音 ----
    "voice": "",                 # 音色 ID，留空就听 voice.txt 的（再没有就是 Cherry）
    "rate": 1.2,                 # 语速，只对克隆音色有效
}

# 界面上调称呼字段时，旁边该显示什么说明
FIELD_HINTS = {
    "user_name": "教练知道了你的名字，念出来才像在跟你说话",
    "user_title": "教练开口第一声叫你什么，比如：指挥官 / 亲爱的驾驶员",
    "coach_name": "显示在卡片左上角抬头，比如：D.VA / 战术参谋",
    "coach_self": "教练提到自己时怎么说，比如：本机 / 我",
}


def load():
    """读配置。文件不存在、写坏了、或者缺字段，都用默认值顶上。"""
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            # 只收认识的键，防止手改时不小心塞进垃圾字段
            cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [配置] config.json 读不出来（{e}），这次先按默认设定跑")
    return cfg


def save(cfg):
    """写回去。只存跟默认值不同的，文件里看着干净。"""
    out = {k: v for k, v in cfg.items() if k in DEFAULTS and v != DEFAULTS[k]}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return CONFIG_PATH


def render_prompt(cfg, layout="", n=8, gap=1.0):
    """把占位符换成真实值，拼成要发给模型的那段话。

    占位符写错了不会炸 —— 找不到就原样留着，免得手滑打错一个字整个功能就废了。
    """
    tpl = cfg.get("prompt") or DEFAULTS["prompt"]
    values = dict(layout=layout, n=n, gap=f"{gap:.2f}",
                  user_name=cfg.get("user_name", ""),
                  user_title=cfg.get("user_title", ""),
                  coach_name=cfg.get("coach_name", ""),
                  coach_self=cfg.get("coach_self", ""))
    try:
        return tpl.format(**values)
    except (KeyError, IndexError, ValueError):
        # 模板里有不认识的占位符，退化成手工商量：只替换认识的四个
        out = tpl
        for k, v in values.items():
            out = out.replace("{" + k + "}", str(v))
        return out
