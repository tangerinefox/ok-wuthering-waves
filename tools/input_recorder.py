"""输入录制器（图形界面版）。

自己点开就能用，不需要别人帮你启动。

流程
----
    点【开始录制】 → 3 秒倒计时（这段时间不录）
    → 窗口变成一个置顶的悬浮条，显示 ● 录制中 / 已录时长 / 事件数
    → 随便按（包括 Alt+Tab 切窗口，那也是有信息量的动作，照录）
    → 按 F10（或点悬浮条上的【停止】）结束并保存

存档
----
    recordings/record-YYYYmmdd-HHMMSS.json

    原汁原味，不做任何删减——怎么处理是 analyze_recording.py 的事。

启动
----
    python tools/input_recorder.py
    或者双击 tools/录制器.bat

依赖：pip install pynput（tkinter 是 Python 自带的）
"""

from __future__ import annotations

import json
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

try:
    from pynput import keyboard, mouse
except ImportError:
    print('缺少依赖，先装：pip install pynput')
    sys.exit(1)

#: 停止热键
STOP_KEY = 'f10'
#: 倒计时秒数
COUNTDOWN = 3
#: 存档目录
OUT_DIR = Path(__file__).resolve().parent.parent / 'recordings'

# 配色
BG_IDLE = '#1b1d21'
BG_REC = '#14161a'
FG = '#e8eaed'
FG_DIM = '#9aa0a6'
ACCENT = '#8ab4f8'
REC_RED = '#f28b82'


def key_name(key) -> str:
    """把 pynput 的按键转成好读的字符串。"""
    char = getattr(key, 'char', None)
    if char:
        return char.lower()
    name = getattr(key, 'name', None)
    if name:
        return name
    return str(key).replace('Key.', '')


class InputRecorder:
    """负责录。线程安全，两个监听器会同时往里写。"""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._t0: float | None = None
        self._lock = threading.Lock()
        self.on_stop_request = None       # 按了 F10 时回调

    def reset(self) -> None:
        with self._lock:
            self.events.clear()
        self._t0 = None

    def _stamp(self) -> float:
        now = time.perf_counter()
        if self._t0 is None:
            self._t0 = now
        return round(now - self._t0, 4)

    def _record(self, kind: str, **fields) -> None:
        event = {'t': self._stamp(), 'type': kind}
        event.update(fields)
        with self._lock:
            self.events.append(event)

    # ---- 监听回调（pynput 线程）----

    def on_press(self, key) -> None:
        if key_name(key) == STOP_KEY:
            if self.on_stop_request:
                self.on_stop_request()
            return False
        self._record('key_down', key=key_name(key))

    def on_release(self, key) -> None:
        if key_name(key) == STOP_KEY:
            return False
        self._record('key_up', key=key_name(key))

    def on_click(self, x: int, y: int, button, pressed: bool) -> None:
        self._record('mouse_down' if pressed else 'mouse_up',
                     button=str(button).replace('Button.', ''), x=x, y=y)

    # ---- 读数 ----

    def snapshot(self) -> tuple[int, float]:
        """返回 (事件数, 已录秒数)。给界面刷新用。"""
        with self._lock:
            count = len(self.events)
            if not self.events:
                return 0, 0.0
            return count, self.events[-1]['t']

    def save(self) -> Path:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        path = OUT_DIR / f'record-{stamp}.json'
        with self._lock:
            events = list(self.events)
        payload = {
            'recorded_at': datetime.now().isoformat(timespec='seconds'),
            'duration': events[-1]['t'] if events else 0.0,
            'event_count': len(events),
            'events': events,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
        return path


class RecorderApp:
    """界面。三个状态：待机 → 倒计时 → 录制中。"""

    def __init__(self) -> None:
        self.recorder = InputRecorder()
        self.recorder.on_stop_request = self._request_stop

        self.key_listener = None
        self.mouse_listener = None
        self.state = 'idle'
        self._saved_path: Path | None = None
        self._drag = (0, 0)

        self.root = tk.Tk()
        self.root.title('输入录制器')
        self.root.configure(bg=BG_IDLE)
        self.root.geometry('360x260')
        self.root.resizable(False, False)

        self._build_idle()

    # ------------------------------------------------------------------ #
    # 待机界面
    # ------------------------------------------------------------------ #

    def _build_idle(self) -> None:
        for widget in self.root.winfo_children():
            widget.destroy()

        self._restore_window()

        tk.Label(self.root, text='输入录制器', bg=BG_IDLE, fg=FG,
                 font=('Microsoft YaHei UI', 15, 'bold')).pack(pady=(28, 4))
        tk.Label(self.root, text='录一段真实操作，用来量连招的时序',
                 bg=BG_IDLE, fg=FG_DIM, font=('Microsoft YaHei UI', 9)).pack()

        tk.Button(self.root, text='开始录制', command=self._start_countdown,
                  bg=ACCENT, fg='#0b0d10', activebackground='#aecbfa',
                  font=('Microsoft YaHei UI', 12, 'bold'), relief='flat',
                  padx=28, pady=10, cursor='hand2').pack(pady=22)

        if self._saved_path:
            tk.Label(self.root, text=f'上次保存：{self._saved_path.name}',
                     bg=BG_IDLE, fg=FG_DIM, font=('Microsoft YaHei UI', 8),
                     wraplength=320).pack()

        tk.Label(self.root, text=f'点开始后有 {COUNTDOWN} 秒倒计时，然后随便按\n'
                                 f'按 {STOP_KEY.upper()} 结束并保存',
                 bg=BG_IDLE, fg=FG_DIM, font=('Microsoft YaHei UI', 8),
                 justify='center').pack(side='bottom', pady=12)

    def _restore_window(self) -> None:
        """从悬浮条恢复成正常窗口。"""
        self.root.overrideredirect(False)
        self.root.attributes('-topmost', False)
        self.root.attributes('-alpha', 1.0)
        self.root.geometry('360x260')

    # ------------------------------------------------------------------ #
    # 倒计时
    # ------------------------------------------------------------------ #

    def _start_countdown(self) -> None:
        self.state = 'countdown'
        self.recorder.reset()
        self._saved_path = None

        for widget in self.root.winfo_children():
            widget.destroy()

        tk.Label(self.root, text='准备…', bg=BG_IDLE, fg=FG_DIM,
                 font=('Microsoft YaHei UI', 10)).pack(pady=(58, 6))
        self.count_label = tk.Label(self.root, text=str(COUNTDOWN), bg=BG_IDLE,
                                    fg=ACCENT, font=('Microsoft YaHei UI', 62, 'bold'))
        self.count_label.pack()
        tk.Label(self.root, text='倒计时结束后开始录', bg=BG_IDLE, fg=FG_DIM,
                 font=('Microsoft YaHei UI', 9)).pack(pady=6)

        self.root.attributes('-topmost', True)
        self._countdown_tick(COUNTDOWN)

    def _countdown_tick(self, n: int) -> None:
        if n > 0:
            self.count_label.config(text=str(n))
            self.root.after(1000, lambda: self._countdown_tick(n - 1))
        else:
            self.count_label.config(text='开始')
            self.root.after(300, self._begin_recording)

    # ------------------------------------------------------------------ #
    # 录制中（悬浮条）
    # ------------------------------------------------------------------ #

    def _begin_recording(self) -> None:
        self.state = 'recording'
        self._build_overlay()
        self._start_listeners()

    def _build_overlay(self) -> None:
        """置顶无边框悬浮条，可拖动。"""
        for widget in self.root.winfo_children():
            widget.destroy()

        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.88)
        self.root.geometry('250x76+40+40')
        self.root.configure(bg=BG_REC)

        frame = tk.Frame(self.root, bg=BG_REC, highlightbackground='#2c2f34',
                         highlightthickness=1)
        frame.pack(fill='both', expand=True)

        top = tk.Frame(frame, bg=BG_REC)
        top.pack(fill='x', padx=12, pady=(10, 0))

        tk.Label(top, text='●', bg=BG_REC, fg=REC_RED,
                 font=('Microsoft YaHei UI', 12)).pack(side='left')
        tk.Label(top, text='录制中', bg=BG_REC, fg=FG,
                 font=('Microsoft YaHei UI', 10, 'bold')).pack(side='left', padx=(5, 0))

        self.time_label = tk.Label(top, text='0.0s', bg=BG_REC, fg=FG,
                                   font=('Consolas', 12, 'bold'))
        self.time_label.pack(side='right')

        self.count_label2 = tk.Label(frame, text='事件 0', bg=BG_REC, fg=FG_DIM,
                                     font=('Microsoft YaHei UI', 8))
        self.count_label2.pack(anchor='w', padx=12, pady=(2, 0))

        tk.Label(frame, text=f'按 {STOP_KEY.upper()} 停止并保存', bg=BG_REC, fg=FG_DIM,
                 font=('Microsoft YaHei UI', 8)).pack(anchor='w', padx=12, pady=(2, 0))

        # 拖动 + 点击停止
        for widget in (frame, top, self.time_label, self.count_label2):
            widget.bind('<Button-1>', self._on_press)
            widget.bind('<B1-Motion>', self._on_drag)

        self._tick()

    def _on_press(self, event) -> None:
        self._drag = (event.x, event.y)

    def _on_drag(self, event) -> None:
        x = event.x_root - self._drag[0]
        y = event.y_root - self._drag[1]
        self.root.geometry(f'+{x}+{y}')

    def _tick(self) -> None:
        """刷新悬浮条上的读数。"""
        if self.state != 'recording':
            return
        count, seconds = self.recorder.snapshot()
        self.time_label.config(text=f'{seconds:.1f}s')
        self.count_label2.config(text=f'事件 {count}')
        self.root.after(100, self._tick)

    def _start_listeners(self) -> None:
        self.key_listener = keyboard.Listener(
            on_press=self.recorder.on_press, on_release=self.recorder.on_release)
        self.mouse_listener = mouse.Listener(on_click=self.recorder.on_click)
        self.key_listener.start()
        self.mouse_listener.start()

    def _stop_listeners(self) -> None:
        for listener in (self.key_listener, self.mouse_listener):
            if listener is not None:
                listener.stop()
        self.key_listener = self.mouse_listener = None

    # ------------------------------------------------------------------ #
    # 收尾
    # ------------------------------------------------------------------ #

    def _request_stop(self) -> None:
        """F10 是在 pynput 线程里触发回调的，界面操作要绕回主线程。"""
        self.root.after(0, self._finish)

    def _finish(self) -> None:
        if self.state != 'recording':
            return
        self.state = 'idle'
        self._stop_listeners()
        # 留一点时间让监听器把最后几个事件写完，再落盘
        self.root.after(150, self._do_save)

    def _do_save(self) -> None:
        count, seconds = self.recorder.snapshot()
        if count == 0:
            self._saved_path = None
            self._build_idle()
            return
        self._saved_path = self.recorder.save()
        self._build_done(seconds, count)

    def _build_done(self, seconds: float, count: int) -> None:
        for widget in self.root.winfo_children():
            widget.destroy()
        self._restore_window()

        tk.Label(self.root, text='✓ 已保存', bg=BG_IDLE, fg='#81c995',
                 font=('Microsoft YaHei UI', 16, 'bold')).pack(pady=(34, 8))
        tk.Label(self.root, text=f'{seconds:.1f} 秒 ｜ {count} 个事件',
                 bg=BG_IDLE, fg=FG, font=('Microsoft YaHei UI', 11)).pack()
        tk.Label(self.root, text=self._saved_path.name if self._saved_path else '',
                 bg=BG_IDLE, fg=FG_DIM, font=('Consolas', 9)).pack(pady=6)

        tk.Button(self.root, text='再录一段', command=self._start_countdown,
                  bg='#2c2f34', fg=FG, activebackground='#3c4043',
                  font=('Microsoft YaHei UI', 10), relief='flat',
                  padx=20, pady=6, cursor='hand2').pack(pady=14)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    RecorderApp().run()


if __name__ == '__main__':
    main()