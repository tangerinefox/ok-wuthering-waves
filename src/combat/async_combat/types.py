"""异步战斗引擎：数据结构定义。

设计原则
--------
这里只定义"好东西长什么样"，不涉及任何执行细节。
一个盲打段（BlindStep）由三部分组成：

    动作序列（actions）—— 按时间戳依次敲出去的输入
    校验点（checks）   —— 中途要确认的状态，便宜优先
    失败后去向（on_fail）—— 校验连续不过时退回哪里

时间一律用"相对段起点的秒数"，方便直接从录制数据里灌。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ActionKind(str, Enum):
    """一次输入的类型。"""

    CLICK = 'click'                # 点一下（鼠标键）
    KEY = 'key'                    # 敲一下键盘
    HOLD_START = 'hold_start'      # 开始长按（不松手）
    HOLD_END = 'hold_end'          # 松开长按


class CheckKind(str, Enum):
    """一次校验的手段。贵的少用，便宜的多用。"""

    COLOR = 'color'          # 取色：只看一个点的颜色，几微秒
    TEMPLATE = 'template'    # 模板匹配：走 find_one，几十毫秒
    OCR = 'ocr'              # 文字识别：最贵，只在关键节点用


class StepResult(str, Enum):
    """一段跑完的结果。"""

    OK = 'ok'                # 全部校验通过，正常走完
    ABORTED = 'aborted'      # 中途判定失败，提前中断
    TIMEOUT = 'timeout'      # 超时未完成
    NOT_STARTED = 'not_started'


@dataclass(slots=True)
class Action:
    """一次要发出去的输入。

    t 是相对段起点的时间（秒）。引擎会等到这个时间点再发，
    所以整段是一张"时间表"，不需要每一步都回头看屏幕。

    例：点击(t=0.0) 点击(t=0.15) 长按(t=0.60, 持续=1.20)
    """

    t: float
    kind: ActionKind = ActionKind.CLICK
    key: str = 'left'            # 鼠标左/中/右键，或键盘键名（如 'e' / 'r' / '1'）
    down_time: float = 0.01      # 按下保持时长
    note: str = ''               # 人看的备注，便于读日志

    def __post_init__(self) -> None:
        if self.t < 0:
            raise ValueError(f'动作时间不能为负: {self.t}')


@dataclass(slots=True)
class Check:
    """一个校验点。

    引擎在 t 时刻去看一眼，判断画面是否符合预期。
    `expect_present=True` 表示"这个图标/这块颜色应该亮着"。

    tolerance 是"连续几次不符才判定失败"——单次识别会因为
    动画遮挡、图标闪烁而误判，所以必须容错。
    """

    t: float
    name: str                    # 校验什么（Labels 里的名字，或自定义颜色键）
    kind: CheckKind = CheckKind.COLOR
    expect_present: bool = True
    tolerance: int = 2           # 连续多少次不符才判失败
    note: str = ''

    def __post_init__(self) -> None:
        if self.t < 0:
            raise ValueError(f'校验时间不能为负: {self.t}')
        if self.tolerance < 1:
            raise ValueError('tolerance 至少为 1')


@dataclass(slots=True)
class BlindStep:
    """一段盲打。

    这是整个框架的核心单位：一段有把握的连招，闭着眼敲完，
    中途只做几次轻量校验，打完再决定下一步去哪。
    """

    name: str
    actions: list[Action] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    on_fail: str = ''            # 失败后回到哪个状态名（空表示由调用方决定）
    on_success: str = ''         # 成功后去哪个状态名（空表示由调用方决定）
    max_duration: float = 0.0    # 整段最长允许耗时；0 表示按最后一个动作自动推算

    def __post_init__(self) -> None:
        self.actions.sort(key=lambda a: a.t)
        self.checks.sort(key=lambda a: a.t)
        if self.max_duration <= 0:
            last_action = self.actions[-1].t if self.actions else 0.0
            last_check = self.checks[-1].t if self.checks else 0.0
            # 自动推算时留 0.5 秒余量，避免卡在理论极限上
            self.max_duration = max(last_action, last_check) + 0.5

    @property
    def duration(self) -> float:
        """按时间表推算的理论时长（不含余量）。"""
        last_action = self.actions[-1].t if self.actions else 0.0
        last_check = self.checks[-1].t if self.checks else 0.0
        return max(last_action, last_check)

    def describe(self) -> str:
        return (f'{self.name}: {len(self.actions)} 个动作 / {len(self.checks)} 个校验点 / '
                f'理论 {self.duration:.2f}s, 上限 {self.max_duration:.2f}s')


@dataclass
class EngineStats:
    """跑过之后的统计，用来调参。"""

    steps_run: int = 0
    steps_ok: int = 0
    steps_aborted: int = 0
    steps_timeout: int = 0
    total_actions: int = 0
    abort_reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f'跑了 {self.steps_run} 段：成功 {self.steps_ok} / '
                f'中断 {self.steps_aborted} / 超时 {self.steps_timeout}；'
                f'共发 {self.total_actions} 次输入')