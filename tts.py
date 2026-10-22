"""语音层：把复盘结论念出来。

为什么要用 Qt 播，而不是装别的库：
    界面已经是 PySide6 的，QtMultimedia 就在里面，不用再装 pyaudio 之类的东西。
    实测过默认输出设备支持 24000Hz / 单声道 / 16 位，正好是合成出来的格式。

为什么必须"边合成边播"：
    实测 23 个字的结论，音频总长 4.39 秒，而全部合成完只要 1.33 秒 ——
    合成速度是播放速度的 3.3 倍。也就是说：
        - 等整段合成好了再播 = 用户先干等 1.3 秒
        - 收到第一块就开播 = 0.5 秒出声，后面永远有货
    所以这里是来一块喂一块，音频设备自己按播放速度去取。

关于刹不住的长度（接上语音后第一条硬约束）：
    实测：22 字的结论念 3.7 秒，56 字的结论念 10.2 秒 —— 中文大约每字 0.18 秒。
    阵亡等待只有 5~15 秒，再算上前面 2~3 秒的分析延迟，
    结论一旦超过 40 字，基本来不及在重生前听完。
    所以 max_tokens 要从 120 往下压（建议 60），这是让语音真正能落地的代价。

关于语速旋钮（2026-10-22 换到 CosyVoice 后重新可用）：
    旧模型 qwen3-tts-flash 的 speed 参数是摆设（1.2 和 1.0 播完时间一样，
    1.5 反而更长），所以当时干脆没留旋钮。
    换到 cosyvoice-v3.5-flash 之后 speech_rate 是真起作用的，22 字实测：
        1.0 -> 6.62s   1.2 -> 5.52s   1.4 -> 4.73s   1.6 -> 4.14s
    这是白捡的收益：同样的字，念完快了三分之一，阵亡窗口够用多了。

两条路，靠音色的长相自动选：
    1) 克隆音色（voice 是一串 xxx-xxx-hash）-> cosyvoice-v3.5-flash，支持语音克隆 + 语速
    2) 预置音色（Cherry/Serena/Ethan/Chelsie）-> qwen3-tts-flash，不支持语速
    音色 ID 里自带模型名前��（cosyvoice-v3.5-flash-coach-xxxx），
    所以模型名是从 ID 反推的 —— 百炼要求音色和模型必须配套，写死会报错。

单独试听：
    python tts.py "念点什么"
    python tts.py "念点什么" --voice Ethan
    python tts.py "念点什么" --voice cosyvoice-v3.5-flash-coach-xxxx --rate 1.4
"""
import argparse
import base64
import os
import re
import queue
import threading
import time

from PySide6.QtCore import QByteArray, QIODevice, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

# ⚠ 坑：状态枚举在 PySide6 6.12 里叫 QtAudio，不叫 QAudio。
# 拿 QAudio.IdleState 去比 sink.state() 永远不相等（两个不同的 enum 类），
# 结果就是"播完了但程序不知道播完了"。旧版本没有 QtAudio，所以两种都试。
try:
    from PySide6.QtMultimedia import QtAudio as _State
except ImportError:
    from PySide6.QtMultimedia import QAudio as _State
IDLE_STATE = _State.IdleState

HERE = os.path.dirname(os.path.abspath(__file__))

# 两条路，见文件头的说明
LEGACY_MODEL = "qwen3-tts-flash"        # 预置音色走这条（Cherry/Serena/Ethan/Chelsie）
CLONE_MODEL = "cosyvoice-v3.5-flash"    # 克隆音色走这条（支持语音克隆 + 语速）
TTS_MODEL = LEGACY_MODEL                # 默认值，实际用哪个看 voice 的长相
DEFAULT_RATE = 1.2                      # 克隆音色专用；1.0 偏慢，1.4 开始有点赶

PRICE_PER_10K_CHARS = 0.8               # 元/万字符，2026-10-22 核实
# 流式返回的 usage 里 characters 恒为 0，拿不到。所以用单次模式标定了一个近似关系：
#   1字→2  2字→4  8字→16  17字→33  23字→44   ⇒ 中文一个字按 2 个计费字符算
CHARS_PER_CN = 2

SAMPLE_RATE = 24000                  # 实测：单声道 / 16 位 / 24000 Hz
CHANNELS = 1
SAMPLE_BYTES = 2
BYTES_PER_SEC = SAMPLE_RATE * CHANNELS * SAMPLE_BYTES     # 48000

PREBUFFER_MS = 250                   # 攒够这么多毫秒再开播，防止播到一半断粮

# 断句符号：模型流式吐字时，碰到这些就先把前半句送去重合成
_SENT_END = re.compile(r"[。！？；!?\n]")

# 至少得有一个正经字（中文 / 字母 / 数字）才值得送去合成。
# 实测：CosyVoice 遇到只有一个句号、或者只有标点的片段会直接拒收，
# 报 "Please ensure input text is valid"。流式切句很容易切出这种碎渣。
_HAS_WORD = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9]")


def is_clone_voice(v):
    """音色 ID 长这样：cosyvoice-v3.5-flash-coach-02f1c882...（结尾是一串十六进制）。
    预置音色则是 Cherry / Serena 这种普通名字。靠这个区分走哪条路。"""
    return bool(v) and bool(re.match(r"^.+-.+-[0-9a-f]{8,}$", v))


def model_for_voice(voice):
    """百炼要求：音色必须和创建它时的 target_model 配套，写错就报错。
    好在音色 ID 自带模型名前缀，反推出来最保险。"""
    if is_clone_voice(voice):
        return voice.rsplit("-", 2)[0]      # 去掉 -前缀-hash，剩下的就是模型名
    return LEGACY_MODEL


def tts_api_key():
    """语音用的 Key。

    有 key_tts.txt 就优先用它（专门给语音的 Key 放这里），
    没有的话跟视觉模型共用 key.txt。
    """
    for name in ("key_tts.txt", "key.txt"):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            s = open(p, encoding="utf-8").read().strip()
            if s:
                return s
    raise RuntimeError("没找到 API Key：建一个 key.txt（或给语音单独建 key_tts.txt）粘进去")


def available_voices():
    """预置音色（账号实测通过的）。想用克隆音色跑 voice_clone.py --list 看。"""
    return ["Cherry", "Serena", "Ethan", "Chelsie"]


def default_voice():
    """默认音色：voice.txt 里写什么就用什么，没这个文件就用 Cherry。

    克隆完自己的声音之后，把音色 ID 写进 voice.txt（一行），
    以后双击 run.bat 就是自己的声音在念，不用每次带参数。
    """
    p = os.path.join(HERE, "voice.txt")
    if os.path.exists(p):
        s = open(p, encoding="utf-8").read().strip()
        if s:
            return s
    return "Cherry"


class ChunkDevice(QIODevice):
    """音频设备一边从这头读，合成线程一边从那头写。

    Qt 的 QAudioSink 会按播放速度主动调 readData 来要数据，
    没数据就返回空 —— 播放器会"饿"一下，但不会崩。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf = QByteArray()
        self.open(QIODevice.ReadWrite)

    def writeData(self, data):
        self._buf.append(data)
        return len(data)

    def readData(self, maxlen):
        have = self._buf.size()
        if have <= 0 or maxlen <= 0:
            return b""
        n = min(maxlen, have)
        out = bytes(self._buf.data()[:n])
        self._buf.remove(0, n)
        return out

    def bytesAvailable(self):
        return self._buf.size() + super().bytesAvailable()

    def buffered(self):
        return self._buf.size()


def _iter_clone_blocks(model, voice, text, rate, api_key):
    """克隆音色：用 tts_v2 的回调式流式接口。

    它是「调用之后通过回调往外吐」的模型，不是迭代器，
    所以这里拿一个队列把它桥接成生成器，好让后面的代码两种模型一视同仁。
    好处是它直接给裸 PCM，不像旧接口那样要先 base64 解码再剥 WAV 头。
    """
    import dashscope
    from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat, ResultCallback

    dashscope.api_key = api_key
    q = queue.Queue()

    class _Cb(ResultCallback):
        def on_open(self):
            pass

        def on_data(self, data):
            q.put(data)

        def on_complete(self):
            pass

        def on_close(self):
            q.put(None)

        def on_error(self, message):
            q.put(("ERR", str(message)[:300]))

        def on_event(self, message):
            pass

    synth = SpeechSynthesizer(model=model, voice=voice,
                              format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                              speech_rate=rate, callback=_Cb())

    def _run():
        try:
            synth.streaming_call(text)
            synth.streaming_complete()
        except Exception as e:
            q.put(("ERR", f"{type(e).__name__}: {str(e)[:200]}"))
        finally:
            q.put(None)

    threading.Thread(target=_run, daemon=True).start()

    while True:
        item = q.get()
        if item is None:
            break
        if isinstance(item, tuple):
            raise RuntimeError(item[1])
        yield item


def _iter_legacy_blocks(model, voice, text, api_key):
    """预置音色：旧接口，迭代器式流式返回。

    注意两点坑：
    - 流式返回里 audio 是 dict 不是对象，只能用 ['data'] 取
    - 第一块带 44 字节 WAV 头，要剥掉，后面全是裸 PCM
    """
    import dashscope
    from dashscope.audio.qwen_tts import SpeechSynthesizer

    dashscope.api_key = api_key
    for resp in SpeechSynthesizer.call(model=model, text=text, voice=voice,
                                       stream=True):
        d = resp.output.audio["data"]
        if not d:
            continue
        b = base64.b64decode(d)
        if b[:4] == b"RIFF":
            i = b.find(b"data")
            b = b[i + 8:] if i >= 0 else b[44:]
        yield b


class Speaker(QObject):
    """把文字变成声音。用法：
        sp = Speaker()
        sp.feed("模型吐的一个字一个字")   # 边生成边喂，攒够一句就开念
        sp.finish()                        # 模型说完了，把尾巴也念掉
    """

    started = Signal(float)      # 出声了，参数是"从开始到出声"的秒数
    finished = Signal(float)     # 全部念完，参数是总秒数
    failed = Signal(str)

    def __init__(self, voice="Cherry", model=None, rate=DEFAULT_RATE):
        super().__init__()
        self.voice = voice
        # 不传 model 就按音色的长相自己挑（克隆音色必须配它自己的模型）
        self.model = model or model_for_voice(voice)
        self.rate = rate      # 只对克隆音色有效，旧模型的语速参数是摆设

        self._pending = ""                # 攒着还没断句的文字
        self._tasks = queue.Queue()       # 待合成的句子
        self._lock = threading.Lock()
        self._pool = []                   # 合成好的 PCM，等主线程取走
        self._gen_done = False
        self._started_ = False
        self._t0 = None
        self._chars = 0                   # 计费字符数
        self._first_sound = None

        self.device = ChunkDevice(self)
        self.sink = None
        self._worker = None

        self._timer = QTimer(self)
        self._timer.setInterval(12)
        self._timer.timeout.connect(self._pump)

    # ---------------------------------------------------------- 对外
    def prime(self):
        """提前把合成线程拉起来。加载 dashscope 那几百毫秒可以跟
        视觉模型的 2 秒思考同时跑掉 —— 别等字都出来了才开始加载库。"""
        self._begin()

    def speak(self, text):
        """一次性把整段念完（不接流式时用这个）。"""
        self._begin()
        self._tasks.put(text)
        self._tasks.put(None)

    def feed(self, chunk):
        """模型流式吐字时一个字一个字喂进来。"""
        self._begin()
        self._pending += chunk
        # 断句就送走；没碰到句号但攒太长了也强行送走，别让用户干等
        while True:
            m = _SENT_END.search(self._pending)
            if m:
                cut = m.end()
                sentences, self._pending = self._pending[:cut], self._pending[cut:]
            elif len(self._pending) >= 40:
                sentences, self._pending = self._pending, ""
            else:
                break
            if sentences and _SENT_END.search(sentences):
                self._tasks.put(sentences)

    def finish(self):
        """模型说完了：把剩下的零碎也念掉，然后等全部播完。"""
        tail = self._pending.strip()
        self._pending = ""
        if tail:
            self._tasks.put(tail)
        self._tasks.put(None)

    def stop(self):
        try:
            self._timer.stop()
            if self.sink is not None:
                self.sink.stop()
        except Exception:
            pass

    @property
    def characters(self):
        return self._chars

    def cost_text(self):
        """这里是估算：流式拿不到真实 usage，只能按标定出来的 ×2 换算。"""
        if not self._chars:
            return ""
        billed = self._chars * CHARS_PER_CN
        c = billed / 10000 * PRICE_PER_10K_CHARS
        return (f"语音约 {billed} 计费字符 => 约 {c*100:.3f} 分钱"
                f"（流式拿不到真实用量，此为估算）")

    # ---------------------------------------------------------- 内部
    def _begin(self):
        if self._t0 is not None:
            return
        self._t0 = time.time()
        for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                   "http_proxy", "https_proxy", "all_proxy"):
            os.environ.pop(_k, None)   # 机器上的代理是死的，一律直连
        self._worker = threading.Thread(target=self._gen_loop, daemon=True)
        self._worker.start()
        self._timer.start()

    def _gen_loop(self):
        """合成线程：一句一句做，把拿到的 PCM 扔进池子给主线程取。"""
        try:
            api_key = tts_api_key()
            while True:
                piece = self._tasks.get()
                if piece is None:
                    break
                if not _HAS_WORD.search(piece):
                    continue          # 纯标点的碎渣，跳过，别白跑一次请求
                self._chars += len(piece)
                if is_clone_voice(self.voice):
                    gen = _iter_clone_blocks(self.model, self.voice, piece,
                                             self.rate, api_key)
                else:
                    gen = _iter_legacy_blocks(self.model, self.voice, piece,
                                              api_key)
                try:
                    for b in gen:
                        with self._lock:
                            self._pool.append(b)
                except Exception as e:
                    # 这一句没合成成功就算了，别让后面的句子跟着一起哑掉
                    self.failed.emit(f"有一句没念出来：{type(e).__name__}:"
                                     f"{str(e)[:80]}")
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {str(e)[:160]}")
        finally:
            self._gen_done = True

    def _pump(self):
        """主线程：把池子里的 PCM 喂给音频设备，攒够了就开播。"""
        with self._lock:
            blocks, self._pool = self._pool, []
        if blocks:
            self.device.writeData(b"".join(blocks))
        buffered = self.device.buffered()

        if not self._started_:
            if buffered >= BYTES_PER_SEC * PREBUFFER_MS / 1000:
                self._open_sink()
                self._started_ = True
                self._first_sound = time.time() - self._t0
                self.started.emit(self._first_sound)
            elif self._gen_done and buffered > 0:
                # 全文都很短，攒不到预缓冲量也别干等着
                self._open_sink()
                self._started_ = True
                self._first_sound = time.time() - self._t0
                self.started.emit(self._first_sound)
            return

        if self._gen_done and buffered <= 0 and not blocks:
            # 合成完了、池子空了、设备里的缓冲也见底了
            # 但 Qt 设备里可能还剩最后一小截没放出来 —— 必须等它变成空闲才算完。
            # （之前在这里先停了定时器，结果再也没机会播完信号，是个 bug）
            if self.sink.state() != IDLE_STATE:
                return
            self._timer.stop()
            try:
                self.sink.stop()
            except Exception:
                pass
            self.finished.emit(time.time() - self._t0)

    def _open_sink(self):
        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(CHANNELS)
        fmt.setSampleFormat(QAudioFormat.Int16)
        dev = QMediaDevices.defaultAudioOutput()
        if not dev.isFormatSupported(fmt):
            raise RuntimeError("音频设备不支持 24000Hz/单声道/16位")
        self.sink = QAudioSink(dev, fmt)
        self.sink.start(self.device)


def speak_blocking(text, voice="Cherry", rate=DEFAULT_RATE, timeout=60):
    """没有界面时也能念：自己起一个最小的事件循环，念完才返回。

    控制台模式（--no-overlay）用它。有界面的地方别用它 —— 会卡住。
    """
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    sp = Speaker(voice=voice, rate=rate)
    got = {}
    sp.started.connect(lambda t: print(f"  [出声 {t:.2f}s]", flush=True))
    sp.failed.connect(lambda m: print(f"  !! 语音失败: {m}", flush=True))
    sp.speak(text)

    # 用一个真的事件循环等它念完（手动调 processEvents 不行：
    # QAudioSink 的状态变化要跑在真正的事件循环里才会通知过来）
    loop = QEventLoop()

    def _done(t):
        got["t"] = t
        loop.quit()

    sp.finished.connect(_done)
    QTimer.singleShot(timeout * 1000, loop.quit)   # 兜底，别永远卡在这
    loop.exec()

    if "t" in got:
        print(f"  [播完 {got['t']:.2f}s]  {sp.cost_text()}", flush=True)
    sp.stop()
    return got.get("t")


def _cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="这次走位太靠中间了，下次贴着右边的掩体推进。")
    ap.add_argument("--voice", default="Cherry",
                    help="预置音色: " + "/".join(available_voices())
                         + "；或克隆音色 ID（跑 voice_clone.py --list 看）")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE,
                    help=f"语速，默认 {DEFAULT_RATE}。只对克隆音色有效"
                         f"（1.0 偏慢，1.4 开始有点赶）")
    ap.add_argument("--file", default=None, help="从文件读文本")
    a = ap.parse_args()

    text = open(a.file, encoding="utf-8").read().strip() if a.file else a.text

    from PySide6.QtWidgets import QApplication
    app = QApplication([])

    sp = Speaker(voice=a.voice, rate=a.rate)
    sp.started.connect(lambda t: print(f"  出声 {t:.2f}s", flush=True))
    sp.finished.connect(lambda t: (print(f"  播完 {t:.2f}s", flush=True),
                                   print(f"  {sp.cost_text()}", flush=True)))
    sp.finished.connect(app.quit)
    sp.failed.connect(lambda m: (print(f"  !! 合成失败: {m}", flush=True), app.quit()))

    print("=" * 60)
    print(f"  文本 {len(text)} 字 | 音色 {a.voice}")
    print(f"  模型 {sp.model} | 语速 {a.rate}")
    print("=" * 60)
    sp.speak(text)

    QTimer.singleShot(30000, app.quit)   # 兜底：30 秒无论如何退出
    app.exec()


if __name__ == "__main__":
    _cli()
