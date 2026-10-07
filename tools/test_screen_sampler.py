"""采样器自测：造一个会变色的窗口，看采样器能不能抓到变化。

会弹一个小窗，几秒后自动关闭并打印结果。
不需要游戏，也不需要真的盯游戏画面。

关键：采样区**不写死坐标**，而是等窗口显示出来后问它自己的实际位置。
写死坐标太容易错——窗口管理器会加边框、标题栏，终端还可能压在上面。
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from screen_sampler import Region, ScreenSampler

WINDOW_POS = (160, 160)
WINDOW_SIZE = (240, 120)
TOGGLE_MS = 400          # 每 400 毫秒变一次色
RUN_SECONDS = 4.0

state = {'on': True, 'count': 0}
sampler: ScreenSampler | None = None
region: Region | None = None

root = tk.Tk()
root.title('采样器自测')
root.geometry(f'{WINDOW_SIZE[0]}x{WINDOW_SIZE[1]}+{WINDOW_POS[0]}+{WINDOW_POS[1]}')
root.configure(bg='#111')
root.attributes('-topmost', True)

panel = tk.Label(root, text='红', bg='#e53935', fg='white',
                 font=('Microsoft YaHei UI', 30, 'bold'))
panel.pack(fill='both', expand=True, padx=3, pady=3)


def toggle() -> None:
    state['on'] = not state['on']
    state['count'] += 1
    if state['on']:
        panel.config(bg='#e53935', text='红')
    else:
        panel.config(bg='#1e88e5', text='蓝')
    root.after(TOGGLE_MS, toggle)


def setup() -> None:
    """窗口显示出来之后再动手——这时候拿到的坐标才是真的。"""
    global sampler, region

    root.update_idletasks()
    left = root.winfo_rootx()
    top = root.winfo_rooty()
    width = root.winfo_width()
    height = root.winfo_height()

    # winfo_rootx/rooty 给的是含标题栏的左上角，往下让开标题栏
    title_bar = height - panel.winfo_height()
    box = (left + 6, top + title_bar + 2, width - 12, panel.winfo_height() - 4)
    region = Region(name='test_panel', box=box, threshold=8.0)

    print(f'窗口实际位置：({left}, {top}) 尺寸 {width}x{height}')
    print(f'采样区：{box}')

    sampler = ScreenSampler(regions=[region], fps=20.0)
    try:
        sampler.start()
    except RuntimeError as exc:
        print(f'启动失败：{exc}\n提示：pip install mss')
        root.destroy()
        return

    print(f'采样器：{sampler.describe()}')
    print(f'开始采样 {RUN_SECONDS:g} 秒（每 {TOGGLE_MS} 毫秒变一次色）...')

    root.after(200, toggle)
    root.after(int(RUN_SECONDS * 1000), finish)


def finish() -> None:
    if sampler is None:
        root.destroy()
        return

    sampler.stop()
    events = sampler.poll()
    expected = int(RUN_SECONDS * 1000 / TOGGLE_MS)
    actual_rate = sampler.sample_count / RUN_SECONDS

    # 存一张采样区的图，方便排查"到底截到了什么"
    debug_note = ''
    try:
        from PIL import Image

        from screen_sampler import ScreenGrabber
        shot = ScreenGrabber().grab(region.box)     # type: ignore[union-attr]
        if shot is not None:
            debug_path = Path(__file__).resolve().parent.parent / 'recordings' / '_sampler_debug.png'
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(shot).save(debug_path)
            debug_note = f'采样区截图：{debug_path}'
    except Exception as exc:
        debug_note = f'存图失败：{exc}'

    print()
    print('=' * 58)
    print(f'采样次数      {sampler.sample_count}  （实际 {actual_rate:.1f}Hz）')
    print(f'检测到变化    {len(events)} 次')
    print(f'预期变化      约 {expected} 次')
    if debug_note:
        print(debug_note)
    print('=' * 58)
    for event in events[:12]:
        print(f"  t={event['t']:7.3f}s   差异 {event['diff']:6.2f}")

    print()
    if len(events) >= expected * 0.5 and actual_rate >= 10:
        print('✅ 采样器工作正常')
    elif len(events) == 0:
        print('❌ 一次都没抓到——采样区多半没对着窗口')
    else:
        print(f'⚠️ 抓到了但偏少（{len(events)}/{expected}）——可能是采样率不够（PIL 后端较慢，装 mss 可提速）')

    root.destroy()


root.after(600, setup)
root.mainloop()