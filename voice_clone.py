"""声音克隆：拿一段你自己的录音，造一个专属音色。

做一次就行。登记完拿到一串音色 ID，之后所有复盘都用这个声音念。

    python voice_clone.py --list                          看看现在有哪些音色
    python voice_clone.py --enroll 我的录音.m4a           用本地录音登记一个
    python voice_clone.py --enroll-url https://xxx/a.wav  用公网音频登记一个
    python voice_clone.py --try <音色ID> "念这句话试试"    试听
    python voice_clone.py --delete <音色ID>               删掉不用的

登记需要的东西（官方规格，录音时照着来就行）：
    时长 10~20 秒最好（最长 60 秒） | ≥16kHz | 单声道 | WAV/MP3/M4A | ≤10MB
    安静的房间，别有背景音乐和别人的声音，别拿唱歌的录音，正常语速念一段话

关于要不要公网 URL：
    不用。本地文件会被编成 Base64 Data URI 直接送上去（官方接口支持这种传法），
    所以录音完直接指到文件路径就行，不需要先传网盘或 OSS。

API Key：
    优先读 key_tts.txt（专门给语音的），没有就跟视觉模型共用 key.txt。
"""
import argparse
import base64
import mimetypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# 你指定的模型。注意：音色跟模型是绑定的 ——
# 用 cosyvoice-v3.5-flash 登记的音色，只能用 cosyvoice-v3.5-flash 念，不能换模型。
TARGET_MODEL = "cosyvoice-v3.5-flash"

_MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
         ".aac": "audio/aac", ".ogg": "audio/ogg"}


def read_key():
    """语音用的 Key。key_tts.txt 优先，没有就用 key.txt。"""
    for name in ("key_tts.txt", "key.txt"):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            s = open(p, encoding="utf-8").read().strip()
            if s:
                return s
    sys.exit("没找到 API Key：建一个 key.txt（语音单独用就建 key_tts.txt），把 Key 粘进去")


def check_audio(path):
    """登记前先自己过一遍规格，别让服务端用一句看不懂的报错打回来。"""
    if not os.path.exists(path):
        sys.exit(f"找不到这个文件：{path}")
    size = os.path.getsize(path)
    if size > 10 * 1024 * 1024:
        sys.exit(f"文件 {size/1024/1024:.1f} MB，超过 10 MB 上限了，剪短一点或转 mp3")
    ext = os.path.splitext(path)[1].lower()
    if ext not in _MIME:
        sys.exit(f"不支持的格式 {ext}。要 WAV / MP3 / M4A 中的一种")
    print(f"  音频 {os.path.basename(path)}  {size/1024:.0f} KB  {ext}")
    if size < 50 * 1024:
        print("  ⚠ 文件偏小，可能不到 10 秒，克隆效果会比较差")
    return ext


def as_data_uri(path):
    """把本地音频编成 data URI。官方接口认这种写法，就不用先传公网了。"""
    ext = os.path.splitext(path)[1].lower()
    mime = _MIME.get(ext) or mimetypes.guess_type(path)[0] or "audio/wav"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    uri = f"data:{mime};base64,{b64}"
    print(f"  已打包成 Data URI（{len(uri)/1024/1024:.1f} MB）")
    return uri


def enroll(svc, audio_arg, prefix, is_url=False, wait=True):
    """登记一个音色，返回音色 ID。"""
    if is_url:
        url = audio_arg
        print(f"  用公网 URL：{url}")
    else:
        check_audio(audio_arg)
        url = as_data_uri(audio_arg)

    t = time.time()
    voice_id = svc.create_voice(target_model=TARGET_MODEL,
                                prefix=prefix, url=url)
    print(f"\n  音色已创建：{voice_id}   （{time.time()-t:.1f}s）")

    # 顺便设为默认音色，省得每次还要加 --voice
    with open(os.path.join(HERE, "voice.txt"), "w", encoding="utf-8") as f:
        f.write(voice_id)
    print("  已写进 voice.txt，之后默认就用这个声音念。")

    if wait:
        # 刚创建完还是 DEPLOYING，要等它 OK 才能拿去念
        deadline = time.time() + 60
        while time.time() < deadline:
            info = svc.query_voice(voice_id)
            status = (info or {}).get("status", "?")
            if status == "OK":
                print(f"  状态 OK，可以用了")
                break
            print(f"  状态 {status}，等 2 秒再看……")
            time.sleep(2)
        else:
            print("  ⚠ 等了一分钟还没就绪，可能要再等等，或者去控制台看看")
    return voice_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="列出账号里已有的克隆音色")
    ap.add_argument("--enroll", help="本地录音文件路径（会自动转成 Data URI 上传）")
    ap.add_argument("--enroll-url", help="公网可访问的音频 URL")
    ap.add_argument("--prefix", default="myvoice", help="音色名前缀，默认 myvoice")
    ap.add_argument("--try", dest="try_id", help="试听某个音色 ID")
    ap.add_argument("--text", default="这波站位太靠中间了，下回合贴着右边掩体走。")
    ap.add_argument("--rate", type=float, default=1.3, help="试听语速，默认 1.3")
    ap.add_argument("--delete", help="删除某个音色 ID")
    a = ap.parse_args()

    # 代理人变量会让它去连一个早就关掉的端口（WinError 10061），一律直连
    for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
               "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(_k, None)

    import dashscope
    from dashscope.audio.tts_v2 import VoiceEnrollmentService

    dashscope.api_key = read_key()
    svc = VoiceEnrollmentService()

    if a.list:
        voices = svc.list_voices() or []
        if not voices:
            print("  账号里还没有克隆音色。")
        else:
            print(f"  账号里已有 {len(voices)} 个克隆音色：")
            for v in voices:
                print(f"    {v.get('voice_id')}   [{v.get('target_model')}] {v.get('status')}")
        return

    if a.delete:
        svc.delete_voice(a.delete)
        print(f"  已删除 {a.delete}")
        return

    if a.enroll or a.enroll_url:
        vid = enroll(svc, a.enroll or a.enroll_url, a.prefix, is_url=bool(a.enroll_url))
        print(f"\n  下一步，用这个声音念复盘：")
        print(f"    python analyze.py --voice {vid}")
        print(f"  或者把它写成默认音色：")
        print(f"    echo {vid} > voice.txt")
        return

    if a.try_id:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from tts import Speaker
        sp = Speaker(voice=a.try_id, rate=a.rate)
        # 播放是靠 Qt 的定时器一点点喂数据的，必须跑事件循环才会出声
        sp.started.connect(lambda t: print(f"\n  出声 {t:.2f}s", flush=True))
        sp.finished.connect(lambda t: print(f"  播完 {t:.2f}s", flush=True))
        sp.failed.connect(lambda m: print(f"  !! 失败: {m}", flush=True))
        sp.finished.connect(app.quit)
        sp.failed.connect(app.quit)
        QTimer.singleShot(30000, app.quit)      # 兜底，别卡死
        sp.speak(a.text)
        app.exec()
        return

    ap.print_help()


if __name__ == "__main__":
    main()
