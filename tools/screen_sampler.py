"""屏幕采样器：定期看几块区域，只把"变化"记下来。

它解决的问题
------------
光录按键没用——你不知道按下去的时候游戏里发生了什么。
这个采样器跟键鼠录制**共用同一个时钟**，所以两边的事件能对齐到同一条时间轴上。

于是就能看出：**按了什么键，多久之后什么亮了。**

为什么不是录视频
----------------
- 视频：3 分钟几百 MB，帧率飘、压缩糊掉小图标，事后还要解码逐帧比对
- 采样：只存"变化"，几十 KB；时刻精确；不用事后处理

画面不需要存，我们只关心"这块区域在 t 时刻变没变"。

怎么用
------
    sampler = ScreenSampler.from_config('tools/sampler_regions.json')
    sampler.start(clock=recorder.clock)
    ...
    for event in sampler.poll():      # 取新增的变化事件
        ...

区域怎么定义
------------
见 tools/sampler_regions.json。坐标是**屏幕像素**（左上角为原点）。
标定方法见 tools/calibrate_regions.py。

依赖
----
    mss（推荐，快）或 pillow（已有，慢一些）。两个都没有时会报错。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

# ---------------------------------------------------------------------- #
# 截屏后端
# ---------------------------------------------------------------------- #

class ScreenGrabber:
    """截屏。优先 mss（快），退回 PIL。"""

    def __init__(self) -> None:
        self.backend = ''
        self._mss = None

        try:
            import mss                                  # noqa: F401
            self._mss = mss.mss()
            self.backend = 'mss'
            return
        except Exception:
            pass

        try:
            from PIL import ImageGrab                    # noqa: F401
            self.backend = 'pillow'
            return
        except Exception as exc:
            raise RuntimeError('需要 mss 或 pillow 才能截屏') from exc

    def grab(self, region: tuple[int, int, int, int]) -> np.ndarray | None:
        """抓一块区域，返回 RGB 的 numpy 数组。region 是 (left, top, width, height)。"""
        left, top, width, height = region
        if width <= 0 or height <= 0:
            return None
        try:
            if self.backend == 'mss':
                shot = self._mss.grab({'left': left, 'top': top,
                                       'width': width, 'height': height})
                return np.asarray(shot)[:, :, :3][:, :, ::-1]   # BGRA → RGB
            from PIL import ImageGrab
            img = ImageGrab.grab(bbox=(left, top, left + width, top + height))
            return np.asarray(img.convert('RGB'))
        except Exception:
            return None


# ---------------------------------------------------------------------- #
# 指纹
# ---------------------------------------------------------------------- #

def signature(patch: np.ndarray, grid: int = 6) -> np.ndarray:
    """把一块图缩成 grid×grid 的缩略图，当作指纹。

    直接比像素太敏感（一点噪点就算变化），比平均色又太钝（图标变了平均色可能没变）。
    缩略图是折中：既忽略噪点，又保留结构。
    """
    h, w = patch.shape[:2]
    if h < grid or w < grid:
        # 区域太小，直接压成一排
        return patch.reshape(-1, 3).mean(axis=0, keepdims=True).astype(np.float32)

    ph, pw = h // grid, w // grid
    trimmed = patch[:ph * grid, :pw * grid]
    blocks = trimmed.reshape(grid, ph, grid, pw, 3)
    return blocks.mean(axis=(1, 3)).astype(np.float32)


def difference(a: np.ndarray, b: np.ndarray) -> float:
    """两个指纹的差异，0~255 之间。

    取"变化最大的那一格"，**不是整张平均**。

    这一点很关键：游戏里的 buff 图标只占监控区的一小块，如果按整张平均，
    那一小块的变化会被周围没变的像素稀释到几乎为零，根本触发不了阈值——
    看起来采样器在正常工作，其实什么都没抓到。
    """
    if a.shape != b.shape:
        return 255.0
    delta = np.abs(a - b)
    if delta.ndim == 3:
        per_cell = delta.mean(axis=2)          # 每个格子的差异
        return float(per_cell.max())
    return float(delta.max())


# ---------------------------------------------------------------------- #
# 区域定义
# ---------------------------------------------------------------------- #

@dataclass
class Region:
    """一块要盯的区域。

    注意：采样区**不能被别的窗口挡住**。游戏开全屏不会有这个问题；
    如果游戏跑在窗口模式、又被其他窗口压住一部分，那块区域截到的
    就是别人的画面——采样数据会变成垃圾，而且不会报错。
    """

    name: str
    #: 屏幕像素坐标 (left, top, width, height)
    box: tuple[int, int, int, int]
    #: 差异超过这个值才算"变了"
    threshold: float = 8.0
    note: str = ''


# ---------------------------------------------------------------------- #
# 采样器
# ---------------------------------------------------------------------- #

@dataclass
class ScreenSampler:
    """按固定频率盯着几块区域，变了就记一笔。"""

    regions: list[Region]
    #: 采样频率（Hz）。buff 图标的变化至少持续几百毫秒，10Hz 足够。
    fps: float = 10.0
    #: 时钟函数。跟录制器传同一个进来，两边时间轴就对齐了。
    clock: Callable[[], float] | None = None

    _grabber: ScreenGrabber | None = field(default=None, init=False, repr=False)
    _last: dict[str, np.ndarray] = field(default_factory=dict, init=False, repr=False)
    _pending: list[dict] = field(default_factory=list, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _t0: float | None = field(default=None, init=False, repr=False)
    _samples: int = field(default=0, init=False, repr=False)

    # ---- 构造 ----

    @classmethod
    def from_config(cls, path: str | Path, fps: float = 10.0,
                    clock: Callable[[], float] | None = None) -> 'ScreenSampler':
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        regions = [
            Region(name=item['name'],
                   box=tuple(item['box']),
                   threshold=item.get('threshold', 8.0),
                   note=item.get('note', ''))
            for item in data['regions']
        ]
        return cls(regions=regions, fps=fps, clock=clock)

    # ---- 生命周期 ----

    def start(self, clock: Callable[[], float] | None = None) -> None:
        if clock is not None:
            self.clock = clock
        if self.clock is None:
            self._t0 = time.perf_counter()
            self.clock = lambda: time.perf_counter() - self._t0   # type: ignore[assignment]
        self._grabber = ScreenGrabber()
        self._prime()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='screen-sampler', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _prime(self) -> None:
        """先拍一张底图，否则第一次采样会把所有区域都当成"刚变化"。"""
        assert self._grabber is not None
        for region in self.regions:
            patch = self._grabber.grab(region.box)
            if patch is not None:
                self._last[region.name] = signature(patch)

    # ---- 采样循环 ----

    def _loop(self) -> None:
        interval = 1.0 / max(self.fps, 0.1)
        next_at = time.perf_counter()
        while not self._stop.is_set():
            self._sample_once()
            self._samples += 1
            next_at += interval
            sleep_for = next_at - time.perf_counter()
            if sleep_for > 0:
                self._stop.wait(sleep_for)
            else:
                next_at = time.perf_counter()      # 落后了就重新对表，别追赶

    def _sample_once(self) -> None:
        assert self._grabber is not None
        stamp = self.clock()                        # type: ignore[misc]
        for region in self.regions:
            patch = self._grabber.grab(region.box)
            if patch is None:
                continue
            sig = signature(patch)
            previous = self._last.get(region.name)
            self._last[region.name] = sig
            if previous is None:
                continue
            delta = difference(sig, previous)
            if delta >= region.threshold:
                with self._lock:
                    self._pending.append({
                        't': round(stamp, 4),
                        'type': 'screen',
                        'region': region.name,
                        'diff': round(delta, 2),
                    })

    # ---- 取数 ----

    def poll(self) -> list[dict]:
        """取走并清空累积的变化事件。"""
        with self._lock:
            events, self._pending = self._pending, []
        return events

    @property
    def sample_count(self) -> int:
        return self._samples

    def describe(self) -> str:
        backend = self._grabber.backend if self._grabber else '未启动'
        names = ', '.join(r.name for r in self.regions)
        return f'{len(self.regions)} 块区域 [{names}] ｜ {self.fps:g}Hz ｜ 后端 {backend}'