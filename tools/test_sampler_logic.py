"""采样器逻辑自测（不碰屏幕）。

为什么不测真实屏幕
------------------
窗口层级是好几个因素在抢（终端、编辑器、游戏），测出来的"失败"往往是
窗口被挡住了，而不是采样器坏了——那种测试只会浪费时间去排查环境。

所以这里分两层验证：
    1. 指纹与差异计算  —— 用合成图像，能完全验证
    2. 采样循环        —— 用假抓帧器喂预设的画面，能完全验证
真实抓帧那一层已经单独验过（能截到内容、不是全黑），不用重复。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from screen_sampler import Region, ScreenSampler, difference, signature

failures: list[str] = []


def check(label: str, condition: bool, detail: str = '') -> None:
    print(f'  [{"OK" if condition else "FAIL"}] {label}' + (f'  {detail}' if detail else ''))
    if not condition:
        failures.append(label)


def rgb(r: int, g: int, b: int, h: int = 90, w: int = 160) -> np.ndarray:
    return np.full((h, w, 3), [r, g, b], dtype=np.uint8)


print('=' * 60)
print('1. 指纹计算')
print('=' * 60)

red = rgb(229, 57, 53)
blue = rgb(30, 136, 229)
noisy_red = np.clip(red.astype(np.int16) + np.random.randint(-3, 4, red.shape), 0, 255).astype(np.uint8)

sig_red = signature(red)
sig_blue = signature(blue)

check('指纹形状符合预期', sig_red.shape == (6, 6, 3), f'得到 {sig_red.shape}')
check('同一张图差异为 0', difference(sig_red, signature(red)) == 0.0)
check('轻微噪点不触发变化（忽略噪点）', difference(sig_red, signature(noisy_red)) < 3.0,
      f'差异 {difference(sig_red, signature(noisy_red)):.2f}')
check('红蓝差异巨大（不被平均掉）', difference(sig_red, sig_blue) > 100,
      f'差异 {difference(sig_red, sig_blue):.2f}')

print()
print('=' * 60)
print('2. 尺寸不一致时不崩')
print('=' * 60)
check('形状不同返回 255', difference(sig_red, np.zeros((3, 3, 3), dtype=np.float32)) == 255.0)

print()
print('=' * 60)
print('3. 采样循环（喂预设画面）')
print('=' * 60)


class FakeGrabber:
    """按顺序吐预设画面，模拟屏幕在变。"""

    backend = 'fake'

    def __init__(self, frames: list[np.ndarray]) -> None:
        self.frames = frames
        self.index = 0

    def grab(self, box):
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return frame


# 底图被 _prime 吃掉（第 1 帧），剩下的序列里颜色切换了 2 次：红→蓝、蓝→红
frames = [red, red, red, blue, blue, red]

sampler = ScreenSampler(
    regions=[Region('panel', (0, 0, 160, 90), threshold=8.0)],
    fps=100.0,
    clock=lambda: 0.0,
)
sampler._grabber = FakeGrabber(frames)
sampler._prime()                       # 抓底图（第 1 帧）
for _ in range(len(frames) - 1):       # 剩下的逐帧采样
    sampler._sample_once()

events = sampler.poll()
check('检测到 2 次变化', len(events) == 2, f'实际 {len(events)} 次')
check('事件类型正确', all(e['type'] == 'screen' for e in events))
check('记录了区域名', all(e['region'] == 'panel' for e in events))
check('poll 取走后清空', len(sampler.poll()) == 0)

print()
print('=' * 60)
print('4. 阈值能挡住小变化')
print('=' * 60)

slightly_different = rgb(229, 57, 53)
slightly_different[0:5, 0:5] = [255, 255, 255]      # 只改一小块

strict = ScreenSampler(
    regions=[Region('panel', (0, 0, 160, 90), threshold=30.0)],
    fps=100.0, clock=lambda: 0.0)
strict._grabber = FakeGrabber([red, slightly_different])
strict._prime()
strict._sample_once()
check('阈值 30 挡住小改动', len(strict.poll()) == 0)

loose = ScreenSampler(
    regions=[Region('panel', (0, 0, 160, 90), threshold=1.0)],
    fps=100.0, clock=lambda: 0.0)
loose._grabber = FakeGrabber([red, slightly_different])
loose._prime()
loose._sample_once()
check('阈值 1 抓到小改动', len(loose.poll()) == 1)

print()
print('=' * 60)
if failures:
    print(f'❌ 有 {len(failures)} 项没过: {failures}')
    sys.exit(1)
print('✅ 采样器逻辑自测全部通过')