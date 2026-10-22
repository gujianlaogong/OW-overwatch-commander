"""配置界面 —— 改称呼、改提示词、换声音，改完存盘就生效。

启动方式：
    .venv\\Scripts\\python.exe settings.py

它只做一件事：把你的设定写进 config.json。
剩下的事（拼提示词、卡片抬头、选音色）都是 analyze.py / overlay.py / tts.py
各自去读那份配置，界面不参与运行时流程 —— 所以这个窗口关掉也不影响场上跑步的程序。
"""

import os
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QSlider, QSpinBox, QVBoxLayout, QWidget,
)

import config as config_mod
from config import CONFIG_PATH, DEFAULTS, FIELD_HINTS, load, save

# 风格沿用浮层卡片：深色底 + 青绿强调
QSS = """
QWidget       { background: #0d1319; color: #e6f2f0;
               font: 13px "Microsoft YaHei UI"; }
QGroupBox     { border: 1px solid rgba(57, 211, 187, 60); border-radius: 10px;
               margin-top: 20px; padding: 16px 12px 12px 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px;
               color: #39d3bb; font: 13px "Microsoft YaHei UI"; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {
               background: #111b23; border: 1px solid rgba(57,211,187,60);
               border-radius: 6px; padding: 6px 8px; color: #e6f2f0;
               selection-background-color: #1d6f63; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {
               border: 1px solid #39d3bb; }
QPlainTextEdit{ font: 12px "Consolas"; }
QPushButton   { background: #16303a; border: 1px solid rgba(57,211,187,80);
               border-radius: 6px; padding: 7px 16px; color: #e6f2f0; }
QPushButton:hover   { background: #1d4250; border-color: #39d3bb; }
QPushButton:pressed { background: #24555f; }
QPushButton:disabled{ color: #4a6160; border-color: rgba(57,211,187,25);
               background: #101a20; }
QLabel#hint   { color: #5d7a79; font: 11px "Microsoft YaHei UI"; }
QLabel#mono   { color: #5d7a79; font: 11px "Consolas"; }
QSlider::groove:horizontal { height: 4px; background: #1d3340; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px;
               background: #39d3bb; }
"""

PRESET_VOICES = ["Cherry", "Serena", "Ethan", "Chelsie"]

# 音色 ID 是一长串 hash，界面上直接摊开会把下拉框撑爆，显示时掐头留尾
def short_id(vid):
    if not vid or vid in PRESET_VOICES:
        return vid or ""
    head, _, tail = vid.rpartition("-")
    return f"{head}-…{tail[-6:]}" if head else f"…{tail[-6:]}"


class SettingsWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load()
        self._speaker = None
        self.setWindowTitle("阵亡复盘教练 · 设置")
        self.setStyleSheet(QSS)
        self.setMinimumWidth(640)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        root.addWidget(self._build_calls())
        root.addWidget(self._build_prompt())
        root.addWidget(self._build_voice())
        root.addWidget(self._build_capture())
        root.addLayout(self._build_buttons())

    # ---------- 第一组：称呼 ----------
    def _build_calls(self):
        g = QGroupBox("称呼")
        form = QFormLayout(g)
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        self.e_user_name = QLineEdit(self.cfg["user_name"])
        self.e_user_title = QLineEdit(self.cfg["user_title"])
        self.e_coach_name = QLineEdit(self.cfg["coach_name"])
        self.e_coach_self = QLineEdit(self.cfg["coach_self"])

        for key, edit in (("user_name", self.e_user_name),
                          ("user_title", self.e_user_title),
                          ("coach_name", self.e_coach_name),
                          ("coach_self", self.e_coach_self)):
            edit.setToolTip(FIELD_HINTS[key])

        form.addRow("我叫什么", self.e_user_name)
        form.addRow("教练叫我什么", self.e_user_title)
        form.addRow("教练叫什么", self.e_coach_name)
        form.addRow("教练自称什么", self.e_coach_self)

        h = QLabel("鼠标停在输入框上会告诉你这一项用在哪。改完记得点保存。")
        h.setObjectName("hint")
        form.addRow("", h)
        return g

    # ---------- 第二组：提示词 ----------
    def _build_prompt(self):
        g = QGroupBox("提示词")
        v = QVBoxLayout(g)

        self.t_prompt = QPlainTextEdit(self.cfg["prompt"])
        self.t_prompt.setMinimumHeight(190)
        v.addWidget(self.t_prompt)

        h = QLabel("花括号里的词会被替换：{user_name} 你的名字  {user_title} 教练叫你什么  "
                   "{coach_name} 教练叫什么  {coach_self} 教练自称  "
                   "{layout} 界面说明（取 layout.txt）  {n} 帧数  {gap} 帧间隔")
        h.setObjectName("mono")
        h.setWordWrap(True)
        v.addWidget(h)

        row = QHBoxLayout()
        btn_reset = QPushButton("恢复默认提示词")
        btn_reset.clicked.connect(
            lambda: self.t_prompt.setPlainText(DEFAULTS["prompt"]))
        row.addWidget(btn_reset)
        row.addStretch(1)

        btn_open_layout = QPushButton("编辑界面说明 layout.txt")
        btn_open_layout.clicked.connect(self._open_layout)
        row.addWidget(btn_open_layout)
        v.addLayout(row)
        return g

    # ---------- 第三组：声音 ----------
    def _build_voice(self):
        g = QGroupBox("声音")
        v = QVBoxLayout(g)

        top = QHBoxLayout()
        self.c_voice = QComboBox()
        self.c_voice.setEditable(True)          # 允许直接把音色 ID 粘进来
        self.c_voice.setMinimumWidth(360)
        self.c_voice.lineEdit().setPlaceholderText("留空 = 听 voice.txt 的，再没有就是 Cherry")
        self._reload_presets()
        self._select_voice(self.cfg.get("voice", ""))
        top.addWidget(self.c_voice, 1)

        btn_refresh = QPushButton("查我的克隆音色")
        btn_refresh.clicked.connect(self._refresh_clones)
        top.addWidget(btn_refresh)

        btn_pick = QPushButton("选一段录音去克隆")
        btn_pick.clicked.connect(self._enroll_file)
        top.addWidget(btn_pick)
        v.addLayout(top)

        self.l_voice_msg = QLabel("")
        self.l_voice_msg.setObjectName("hint")
        self.l_voice_msg.setWordWrap(True)
        v.addWidget(self.l_voice_msg)

        speed = QHBoxLayout()
        self.s_rate = QSlider(Qt.Horizontal)
        self.s_rate.setRange(5, 16)
        self.s_rate.setValue(int(round(self.cfg.get("rate", 1.2) * 10)))
        self.l_rate = QLabel(f"{self.s_rate.value()/10:.1f} 倍速")
        self.s_rate.valueChanged.connect(
            lambda n: self.l_rate.setText(f"{n/10:.1f} 倍速"))
        speed.addWidget(QLabel("语速"))
        speed.addWidget(self.s_rate, 1)
        speed.addWidget(self.l_rate)

        self.e_try = QLineEdit("这次走位太靠中间了，下次贴着右边的掩体推进。")
        btn_try = QPushButton("试听")
        btn_try.clicked.connect(self._try_voice)
        speed.addWidget(self.e_try, 1)
        speed.addWidget(btn_try)
        v.addLayout(speed)

        h = QLabel("语速只对克隆音色有用；预置的那四个声音调不动，这是模型本身的限制。")
        h.setObjectName("hint")
        v.addWidget(h)
        return g

    # ---------- 第四组：画面与时间 ----------
    def _build_capture(self):
        g = QGroupBox("看多长一段画面")
        form = QFormLayout(g)
        form.setLabelAlignment(Qt.AlignRight)

        self.n_window = QSpinBox()
        self.n_window.setRange(3, 30)
        self.n_window.setValue(int(self.cfg["window"]))
        self.n_window.setSuffix(" 秒")

        self.n_frames = QSpinBox()
        self.n_frames.setRange(2, 16)
        self.n_frames.setValue(int(self.cfg["frames"]))
        self.n_frames.setSuffix(" 帧")

        self.n_tokens = QSpinBox()
        self.n_tokens.setRange(20, 150)
        self.n_tokens.setValue(int(self.cfg["max_tokens"]))
        self.n_tokens.setSuffix(" 字")

        form.addRow("阵亡前看多久", self.n_window)
        form.addRow("抽几张图给模型", self.n_frames)
        form.addRow("结论最多说几个字", self.n_tokens)

        h = QLabel("结论越长越念不完 —— 中文每字约 0.18 秒，60 字就是 10 秒以上。"
                   "阵亡等待一般只有十几秒，还要扣掉前面两秒的分析时间。")
        h.setObjectName("hint")
        h.setWordWrap(True)
        form.addRow("", h)
        return g

    # ---------- 底部按钮 ----------
    def _build_buttons(self):
        row = QHBoxLayout()
        self.b_save = QPushButton("保存")
        self.b_save.clicked.connect(self._save)
        self.b_save.setDefault(True)

        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.close)

        b_reset_all = QPushButton("全部恢复默认")
        b_reset_all.clicked.connect(self._reset_all)

        row.addWidget(self.b_save)
        row.addWidget(b_close)
        row.addStretch(1)
        row.addWidget(b_reset_all)
        return row

    # ---------- 动作 ----------
    def _collect(self):
        """把界面上现在的值收成一份配置字典。"""
        c = dict(self.cfg)
        c["user_name"] = self.e_user_name.text().strip()
        c["user_title"] = self.e_user_title.text().strip()
        c["coach_name"] = self.e_coach_name.text().strip()
        c["coach_self"] = self.e_coach_self.text().strip()
        c["prompt"] = self.t_prompt.toPlainText()
        c["voice"] = self._voice_value()
        c["rate"] = self.s_rate.value() / 10
        c["window"] = float(self.n_window.value())
        c["frames"] = int(self.n_frames.value())
        c["max_tokens"] = int(self.n_tokens.value())
        return c

    def _save(self):
        c = self._collect()
        # 四个称呼不能空着，空了提示词里就会出现「你好， 」这种断句
        missing = [self.tr_name(k) for k in
                   ("user_name", "user_title", "coach_name", "coach_self")
                   if not c[k]]
        if missing:
            QMessageBox.warning(self, "还差一项",
                                "这几项不能留空：\n　" + "、".join(missing))
            return
        path = save(c)
        self.cfg = c
        QMessageBox.information(
            self, "已保存",
            f"写进 {os.path.basename(path)}\n\n"
            "下一次按热键复盘就会用新的设定，正在跑的程序也不用重启。")

    @staticmethod
    def tr_name(k):
        return {"user_name": "我叫什么", "user_title": "教练叫我什么",
                "coach_name": "教练叫什么", "coach_self": "教练自称什么"}[k]

    def _reset_all(self):
        if QMessageBox.question(
                self, "恢复默认",
                "会把所有设定改回出厂值（不影响你已经克隆好的音色列表）。继续？"
        ) != QMessageBox.Yes:
            return
        d = dict(DEFAULTS)
        self.e_user_name.setText(d["user_name"])
        self.e_user_title.setText(d["user_title"])
        self.e_coach_name.setText(d["coach_name"])
        self.e_coach_self.setText(d["coach_self"])
        self.t_prompt.setPlainText(d["prompt"])
        self.s_rate.setValue(int(round(d["rate"] * 10)))
        self.n_window.setValue(int(d["window"]))
        self.n_frames.setValue(int(d["frames"]))
        self.n_tokens.setValue(int(d["max_tokens"]))

    # ---- 音色列表 ----
    def _reload_presets(self):
        self.c_voice.clear()
        # 第一项代表「留空」：存回去是空字符串，运行时才会去兜底读 voice.txt → Cherry。
        # 不能在界面上直接显示 Cherry，否则一保存就把这条兜底链路写死了。
        self.c_voice.addItem("默认（按 voice.txt 的设定）", "")
        for v in PRESET_VOICES:
            self.c_voice.addItem(v, v)

    def _voice_value(self):
        """下拉框现在代表的音色 ID。这里有三个坑，都实测过：

        1. 「默认」那项存的是空字符串 —— 用 `or` 会把它吃掉，错拿成显示文字。
        2. 用户手工粘 ID 时，Qt 的 setEditText 只改显示文字、不动选中项，
           所以 currentData() 仍然是原来那一项的值。得靠「文本是否偏离了这一项」
           来判断现在是手工输入的状态。
        3. 真的没有任何选中时 currentData() 是 None，那也退回用显示文本。
        """
        i = self.c_voice.currentIndex()
        if i < 0:
            return self.c_voice.currentText().strip()
        if self.c_voice.currentText().strip() != self.c_voice.itemText(i).strip():
            return self.c_voice.currentText().strip()
        return self.c_voice.itemData(i) or ""

    def _select_voice(self, vid):
        """按音色 ID 选中；列表里没有就原样摆在输入框里（手工粘 ID 的情况）。"""
        if not vid:
            self.c_voice.setCurrentIndex(0)
            return
        i = self.c_voice.findData(vid)
        if i >= 0:
            self.c_voice.setCurrentIndex(i)
        else:
            self.c_voice.setEditText(vid)

    def _refresh_clones(self):
        self.l_voice_msg.setText("正在问百炼……")
        QApplication.processEvents()
        try:
            from voice_clone import list_clone_voices
            rows = list_clone_voices()
        except Exception as e:
            self.l_voice_msg.setText(f"没查出来：{e}")
            return

        _keep = self._voice_value()
        self._reload_presets()
        for vid, model, status in rows:
            tag = f"{short_id(vid)}   [{model.replace('cosyvoice-', '')}] {status}"
            self.c_voice.addItem(tag, vid)
        self._select_voice(_keep)
        self.l_voice_msg.setText(
            f"查到 {len(rows)} 个克隆音色，已加进下拉框。"
            if rows else "账号里还没有克隆音色。点右边『选一段录音去克隆』传一个。")

    def _enroll_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选一段你的录音", "",
            "音频文件 (*.wav *.mp3 *.m4a *.aac *.ogg);;所有文件 (*.*)")
        if not path:
            return
        self.l_voice_msg.setText(f"正在登记 {os.path.basename(path)}，要等一小会儿……")
        QApplication.processEvents()
        try:
            from voice_clone import enroll_file
            vid = enroll_file(path, prefix="myvoice")
        except Exception as e:
            self.l_voice_msg.setText(f"没登记成功：{e}")
            QMessageBox.warning(self, "登记失败", str(e))
            return
        self.l_voice_msg.setText(f"登记好了：{short_id(vid)}")
        self._refresh_clones()
        self._select_voice(vid)
        QMessageBox.information(
            self, "克隆完成",
            f"音色 ID：{vid}\n\n已经自动选上了，点保存就会用这个声音念复盘。")

    def _try_voice(self):
        text = self.e_try.text().strip()
        if not text:
            QMessageBox.information(self, "没词可念", "先在上面那行写点什么。")
            return
        c = self._collect()
        vid = c["voice"] or ""
        if not vid:
            QMessageBox.information(self, "没选音色", "先在下拉框里挑一个声音。")
            return
        self.l_voice_msg.setText("正在念……")
        try:
            from tts import Speaker
            self._speaker = Speaker(voice=vid, rate=c["rate"])
            self._speaker.started.connect(
                lambda t: self.l_voice_msg.setText(f"出声了（{t:.2f}s），听着。"))
            self._speaker.finished.connect(
                lambda t: self.l_voice_msg.setText(f"念完了，共 {t:.2f}s。"))
            self._speaker.failed.connect(
                lambda m: self.l_voice_msg.setText(f"念不出来：{m}"))
            self._speaker.prime()
            self._speaker.feed(text)
            self._speaker.finish()
        except Exception as e:
            self.l_voice_msg.setText(f"念不出来：{type(e).__name__}: {str(e)[:120]}")

    def _open_layout(self):
        """用系统记事本打开 layout.txt —— 那份界面说明很长，在专门的地方改更顺手。"""
        p = os.path.join(config_mod.HERE, "layout.txt")
        if not os.path.exists(p):
            QMessageBox.information(self, "没有这个文件", f"找不到 {p}")
            return
        try:
            os.startfile(p) if sys.platform == "win32" else subprocess.Popen(["open", p])
        except Exception as e:
            QMessageBox.warning(self, "打不开", str(e))


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    w = SettingsWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
