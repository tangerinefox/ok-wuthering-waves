"""异步战斗引擎。

把"敲键"和"看屏幕"拆到两个线程，让连招不再被识别延迟拖着走。

用法：:

    from src.combat.async_combat import AsyncCombatEngine, FrameVerifier, ColorRule
    from src.combat.async_combat.hsin_steps import build_hsin_cycle

    verifier = FrameVerifier(task, color_rules={...})
    engine = AsyncCombatEngine(task, verifier)
    engine.run_cycle(build_hsin_cycle(), start='红心·攒心→强化重击')

**这套东西和老的串行逻辑完全并存**——不 import 就不会被用到，
原有角色的代码一个字都不用改。
"""

from src.combat.async_combat.engine import AsyncCombatEngine, Verifier
from src.combat.async_combat.types import (
    Action,
    ActionKind,
    BlindStep,
    Check,
    CheckKind,
    EngineStats,
    StepResult,
)
from src.combat.async_combat.verifier import ColorRule, FrameVerifier

__all__ = [
    'AsyncCombatEngine',
    'Verifier',
    'FrameVerifier',
    'ColorRule',
    'Action',
    'ActionKind',
    'BlindStep',
    'Check',
    'CheckKind',
    'EngineStats',
    'StepResult',
]