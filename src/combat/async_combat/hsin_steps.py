"""心的盲打段定义。

⚠️ 重要：**结构是对的，数字全是占位值。**

下面所有时间、次数都没经过实测——现在这台机器上没有游戏，
任何"我觉得大概是 0.3 秒"的猜测都是耍流氓。所以统一用 PLACEHOLDER
标出来，等回到游戏机上标定完再填。

标定流程：
    1. 用 tools/input_recorder.py 录一遍真实操作
    2. 用 tools/analyze_recording.py 抠出每段的真实时长
    3. 把下面 PLACEHOLDER 区里的值换成实测值

循环结构（对应我们讨论的那套，用玩家叫法）：
    红心攒心 → 强化重击 → 按 R 切形态（变黄心）
      → 黄心攒心 → 按 E 进 13 秒 → 烧心 → 长按左键
      → 按 R 放通天之柱 → 回到红心
"""

from __future__ import annotations

from ok.util.logger import Logger

from src.combat.async_combat.types import (
    Action,
    ActionKind,
    BlindStep,
    Check,
    CheckKind,
)

logger = Logger.get_logger(__name__)

# ====================================================================== #
# PLACEHOLDER —— 全部待标定
# ====================================================================== #

#: 红心形态：攒满【应世心】需要几次普攻
RED_CLICKS = 3
#: 普攻之间的间隔（秒）
RED_CLICK_GAP = 0.15
#: 红心强化重击：长按多久（秒）
RED_HEAVY_HOLD = 1.2
#: 红心重击动画大约多久（秒），期间不校验
RED_HEAVY_ANIM = 1.0

#: 黄心形态：攒满【照世心】需要几次普攻
YELLOW_CLICKS = 3
YELLOW_CLICK_GAP = 0.15

#: 13 秒窗口内：把心烧空需要几次普攻
BURN_CLICKS = 4
BURN_CLICK_GAP = 0.15
#: 13 秒窗口内强化重击的长按时长
BURN_HEAVY_HOLD = 1.2

#: 按键之后等动画起来的缓冲
KEY_SETTLE = 0.15

# ====================================================================== #


def _click(t: float, note: str = '') -> Action:
    return Action(t=t, kind=ActionKind.CLICK, key='left', note=note)


def _key(t: float, key: str, note: str = '') -> Action:
    return Action(t=t, kind=ActionKind.KEY, key=key, note=note)


def _hold_start(t: float, note: str = '') -> Action:
    return Action(t=t, kind=ActionKind.HOLD_START, key='left', note=note)


def _hold_end(t: float, note: str = '') -> Action:
    return Action(t=t, kind=ActionKind.HOLD_END, key='left', note=note)


def _check(t: float, label: str, *, kind: CheckKind = CheckKind.TEMPLATE,
           expect: bool = True, tolerance: int = 2, note: str = '') -> Check:
    return Check(t=t, name=label, kind=kind, expect_present=expect,
                 tolerance=tolerance, note=note)


# ====================================================================== #
# 段 1：红心攒心 → 强化重击
# ====================================================================== #

def step_red_buildup() -> BlindStep:
    """红心形态下平A攒【应世心】，满了长按左键放强化重击。

    这一段的产出是"解锁切形态"——按对了，切形态那个图标会亮。
    """
    actions: list[Action] = []
    t = 0.0
    for i in range(RED_CLICKS):
        actions.append(_click(t, f'普攻{i + 1}'))
        t += RED_CLICK_GAP
    actions.append(_hold_start(t, '强化重击开始'))
    actions.append(_hold_end(t + RED_HEAVY_HOLD, '强化重击结束'))

    return BlindStep(
        name='红心·攒心→强化重击',
        actions=actions,
        checks=[
            # 重击动画结束后再看，早看会被动画挡住
            _check(t + RED_HEAVY_HOLD + RED_HEAVY_ANIM, 'hsin_lib1',
                   note='切形态图标应该亮了'),
        ],
        on_fail='红心·攒心→强化重击',
        on_success='切形态',
    )


# ====================================================================== #
# 段 2：按 R 切形态
# ====================================================================== #

def step_formshift() -> BlindStep:
    """按 R 切到黄心形态。这一下有动画，期间不要插别的输入。"""
    return BlindStep(
        name='按R切形态',
        actions=[_key(0.0, 'r', '切形态')],
        checks=[
            _check(KEY_SETTLE + 0.6, 'char_hsin', note='确认还在场（占位校验）'),
        ],
        on_fail='红心·攒心→强化重击',
        on_success='黄心·攒心→按E进13秒',
    )


# ====================================================================== #
# 段 3：黄心攒心 → 按 E 进 13 秒
# ====================================================================== #

def step_yellow_buildup() -> BlindStep:
    """黄心形态下平A攒【照世心】，满了按 E 进 13 秒窗口。"""
    actions: list[Action] = []
    t = 0.0
    for i in range(YELLOW_CLICKS):
        actions.append(_click(t, f'普攻{i + 1}'))
        t += YELLOW_CLICK_GAP
    actions.append(_key(t, 'e', '强化E·进13秒'))

    return BlindStep(
        name='黄心·攒心→按E进13秒',
        actions=actions,
        checks=[
            _check(t + KEY_SETTLE + 0.5, 'hsin_h1',
                   note='强化重击图标应该出现（说明进窗口了）'),
        ],
        on_fail='黄心·攒心→按E进13秒',
        on_success='13秒·烧心→强化重击',
    )


# ====================================================================== #
# 段 4：13 秒窗口内烧心 → 强化重击
# ====================================================================== #

def step_burn_forte() -> BlindStep:
    """13 秒窗口内平A把【照世心】烧空，然后长按左键放强化重击。

    这一段是**最赶的**——13 秒窗口有硬上限，普攻打空就废了。
    校验点故意设得很轻（只看一个图标），免得"看"拖慢"敲"。
    """
    actions: list[Action] = []
    t = 0.0
    for i in range(BURN_CLICKS):
        actions.append(_click(t, f'普攻{i + 1}'))
        t += BURN_CLICK_GAP
    actions.append(_hold_start(t, '强化重击开始'))
    actions.append(_hold_end(t + BURN_HEAVY_HOLD, '强化重击结束'))

    return BlindStep(
        name='13秒·烧心→强化重击',
        actions=actions,
        checks=[
            _check(t + BURN_HEAVY_HOLD + RED_HEAVY_ANIM, 'hsin_lib2',
                   note='通天之柱图标应该亮了'),
        ],
        on_fail='黄心·攒心→按E进13秒',
        on_success='放大招',
    )


# ====================================================================== #
# 段 5：按 R 放通天之柱
# ====================================================================== #

def step_liberation() -> BlindStep:
    """收尾：按 R 放通天之柱。放完变回红心，一轮结束。"""
    return BlindStep(
        name='按R放通天之柱',
        actions=[_key(0.0, 'r', '通天之柱')],
        checks=[
            # 放完会变回红心，这里校验"已经回到红心"（占位）
            _check(KEY_SETTLE + 1.0, 'char_hsin', note='确认收尾（占位校验）'),
        ],
        on_fail='红心·攒心→强化重击',
        on_success='红心·攒心→强化重击',
    )


# ====================================================================== #
# 拼装：完整循环
# ====================================================================== #

def build_hsin_cycle() -> dict[str, BlindStep]:
    """把各段拼成一张"状态表"，键是状态名。

    引擎按 on_success / on_fail 在这些状态之间跳。
    想把某一段换成更极限的版本，就替换掉表里对应的那一项，
    其他段不受影响——这就是分段的好处。
    """
    steps = {
        '红心·攒心→强化重击': step_red_buildup(),
        '切形态': step_formshift(),
        '黄心·攒心→按E进13秒': step_yellow_buildup(),
        '13秒·烧心→强化重击': step_burn_forte(),
        '放大招': step_liberation(),
    }
    logger.info(f'心的循环共 {len(steps)} 段，总理论时长 '
                f'{sum(s.duration for s in steps.values()):.2f}s（占位值）')
    return steps