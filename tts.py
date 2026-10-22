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

    另外试过用 speed 参数加速：1.2 和 1.0 播完时间一样，1.5 反而更长 ——
    这个参数在 qwen3-tts-flash 上不起作用，所以代码里干脆没留这个旋钮。

单独试听：
    python tts.py "念点什么"
    python tts.py "念点什么" --voice Ethan
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

TTS_MODEL = "qwen3-tts-flash"        # 账号里实测可用的（cosyvoice 系列没开通）
PRICE_PER_10K_CHARS = 0.8            # 元/万字符，2026-10-22 核实
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


def available_voices():
    """账号实测通过的音色（longxiaochun 那一批没开通，会报 400）。"""
    return ["Cherry", "Serena", "Ethan", "Chelsie"]


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


class Speaker(QObject):
    """把文字变成声音。用法：
        sp = Speaker()
        sp.feed("模型吐的一个字一个字")   # 边生成边喂，攒够一句就开念
        sp.finish()                        # 模型说完了，把尾巴也念掉
    """

    started = Signal(float)      # 出声了，参数是"从开始到出声"的秒数
    finished = Signal(float)     # 全部念完，参数是总秒数
    failed = Signal(str)

    def __init__(self, voice="Cherry", model=TTS_MODEL):
        super().__init__()
        self.voice = voice
        self.model = model

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
            import dashscope
            dashscope.api_key = open(os.path.join(HERE, "key.txt"),
                                     encoding="utf-8").read().strip()
            from dashscope.audio.qwen_tts import SpeechSynthesizer
            while True:
                piece = self._tasks.get()
                if piece is None:
                    break
                self._chars += len(piece)
                kw = {"model": self.model, "text": piece, "voice": self.voice,
                      "stream": True}
                for resp in SpeechSynthesizer.call(**kw):
                    # 注意：流式返回里 audio 是 dict，不是对象，只能用 ['data']
                    d = resp.output.audio["data"]
                    if not d:
                        continue
                    b = base64.b64decode(d)
                    if b[:4] == b"RIFF":
                        # 第一块带 44 字节的 WAV 头，跳过它只要 PCM
                        i = b.find(b"data")
                        b = b[i + 8:] if i >= 0 else b[44:]
                    with self._lock:
                        self._pool.append(b)
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


def speak_blocking(text, voice="Cherry", timeout=60):
    """没有界面时也能念：自己起一个最小的事件循环，念完才返回。

    控制台模式（--no-overlay）用它。有界面的地方别用它 —— 会卡住。
    """
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    sp = Speaker(voice=voice)
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
    ap.add_argument("--voice", default="Cherry", help="音色: " + "/".join(available_voices()))
    ap.add_argument("--file", default=None, help="从文件读文本")
    a = ap.parse_args()

    text = open(a.file, encoding="utf-8").read().strip() if a.file else a.text

    from PySide6.QtWidgets import QApplication
    app = QApplication([])

    sp = Speaker(voice=a.voice)
    sp.started.connect(lambda t: print(f"  出声 {t:.2f}s", flush=True))
    sp.finished.connect(lambda t: (print(f"  播完 {t:.2f}s", flush=True),
                                   print(f"  {sp.cost_text()}", flush=True)))
    sp.finished.connect(app.quit)
    sp.failed.connect(lambda m: (print(f"  !! 合成失败: {m}", flush=True), app.quit()))

    print("=" * 60)
    print(f"  文本 {len(text)} 字 | 音色 {a.voice}")
    print("=" * 60)
    sp.speak(text)

    QTimer.singleShot(30000, app.quit)   # 兜底：30 秒无论如何退出
    app.exec()


if __name__ == "__main__":
    _cli()
