"""校验器：判断"画面上的某个状态，是不是我们期待的样子"。

两条路线，成本差三个数量级
--------------------------
取色（COLOR）   ：切一块区域出来比颜色，几微秒。适合高频、简单的判断，
                  比如"大招图标亮没亮""技能条满没满"。
模板匹配（TEMPLATE）：走 ok-script 的 find_one，几十毫秒但准。适合关键节点。

帧从哪来
--------
校验器**自己抓帧**，不读执行器缓存的那一帧。原因是缓存帧归帧循环管，
感知线程去读会踩到别人的写入。自己抓一份最干净——代价是每抓一次
要一次系统调用，所以校验点别设太密。

颜色规则怎么配
--------------
每条规则描述"一块区域里应该有多少比例的目标颜色":

    ColorRule('hsin_h1', region=(0.42, 0.86, 0.50, 0.92), rgb=(255, 210, 60))

region 用相对坐标（0~1），跟着分辨率走，换屏幕不用改。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

from ok.util.logger import Logger

from src.combat.async_combat.types import Check, CheckKind

logger = Logger.get_logger(__name__)


@dataclass(slots=True)
class ColorRule:
    """一条取色规则。"""

    name: str
    #: 相对坐标 (x0, y0, x1, y1)，取值 0~1
    region: tuple[float, float, float, float]
    #: 目标颜色，RGB
    rgb: tuple[int, int, int]
    #: 单通道容差，越大越宽松
    color_tolerance: int = 40
    #: 区域内命中该颜色的像素占比超过这个值，就算"亮着"
    min_ratio: float = 0.05
    #: 备注
    note: str = ''

    def __post_init__(self) -> None:
        x0, y0, x1, y1 = self.region
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError(f'{self.name}: 区域坐标非法 {self.region}')


class FrameVerifier:
    """基于截图的校验器。

    :param task: ok-ww 的 task 对象
    :param color_rules: 取色规则表，键是规则名（Check.name 会来这里查）
    :param template_threshold: 模板匹配的阈值
    """

    def __init__(self, task, color_rules: dict[str, ColorRule] | None = None,
                 template_threshold: float = 0.7):
        self.task = task
        self.executor = task.executor
        self.color_rules = dict(color_rules or {})
        self.template_threshold = template_threshold

        self._grab_lock = threading.Lock()
        self._last_frame = None
        self._frame_fail_streak = 0

    # ------------------------------------------------------------------ #
    # 对外
    # ------------------------------------------------------------------ #

    def warm_up(self) -> None:
        """确认抓帧通道可用。失败只告警，不阻断。"""
        frame = self.grab()
        if frame is None:
            logger.warning('校验器预热失败：抓不到帧（可能是游戏没开/窗口没激活）')
        else:
            h, w = frame.shape[:2]
            logger.debug(f'校验器就绪，帧尺寸 {w}x{h}')

    def verify(self, check: Check) -> bool:
        """返回 True 表示画面符合预期。"""
        if check.kind is CheckKind.COLOR:
            present = self._color_present(check.name)
        elif check.kind is CheckKind.TEMPLATE:
            present = self._template_present(check.name)
        else:
            logger.warning(f'暂不支持的校验类型 {check.kind}，视为通过')
            return True

        if present is None:      # 抓不到帧：不判失败，避免误杀
            return True
        return present if check.expect_present else (not present)

    def add_rule(self, rule: ColorRule) -> None:
        self.color_rules[rule.name] = rule

    # ------------------------------------------------------------------ #
    # 抓帧
    # ------------------------------------------------------------------ #

    def grab(self):
        """抓一帧。抓不到返回 None。"""
        method = getattr(self.executor, 'method', None)
        if method is None or not self._connected(method):
            return None
        with self._grab_lock:
            try:
                frame = method.get_frame()
            except Exception as exc:
                self._frame_fail_streak += 1
                if self._frame_fail_streak in (1, 10, 100):
                    logger.warning(f'抓帧失败（第 {self._frame_fail_streak} 次）: {exc}')
                return None
        if frame is None:
            return None
        self._frame_fail_streak = 0
        self._last_frame = frame
        return frame

    @staticmethod
    def _connected(method) -> bool:
        checker = getattr(method, 'connected', None)
        return bool(checker()) if callable(checker) else True

    # ------------------------------------------------------------------ #
    # 取色
    # ------------------------------------------------------------------ #

    def _color_present(self, rule_name: str) -> bool | None:
        rule = self.color_rules.get(rule_name)
        if rule is None:
            logger.warning(f'没有名为 {rule_name} 的取色规则，视为通过')
            return None

        frame = self.grab()
        if frame is None:
            return None

        patch = self._crop(frame, rule.region)
        if patch is None or patch.size == 0:
            return None

        # OpenCV 抓到的帧是 BGR；规则里写的是 RGB，这里换过来
        target = np.array([rule.rgb[2], rule.rgb[1], rule.rgb[0]], dtype=np.int16)
        diff = np.abs(patch.astype(np.int16) - target)
        hit = np.all(diff <= rule.color_tolerance, axis=2)
        ratio = float(hit.mean())

        matched = ratio >= rule.min_ratio
        logger.debug(f'取色 {rule_name}: 占比 {ratio:.3f} (阈值 {rule.min_ratio}) → {matched}')
        return matched

    @staticmethod
    def _crop(frame, region: tuple[float, float, float, float]):
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = region
        px0, py0 = int(x0 * w), int(y0 * h)
        px1, py1 = int(x1 * w), int(y1 * h)
        if px1 <= px0 or py1 <= py0:
            return None
        return frame[py0:py1, px0:px1]

    # ------------------------------------------------------------------ #
    # 模板匹配
    # ------------------------------------------------------------------ #

    def _template_present(self, label: str) -> bool | None:
        """模板匹配。

        注意：这一路走的是 task.find_one，它内部可能需要执行器提供帧，
        在多线程下属于"能用但要小心"的方案。能用取色就别用这个。
        """
        try:
            found = self.task.find_one(label, threshold=self.template_threshold)
        except Exception as exc:
            logger.warning(f'模板匹配 {label} 失败，视为通过: {exc}')
            return None
        return bool(found)