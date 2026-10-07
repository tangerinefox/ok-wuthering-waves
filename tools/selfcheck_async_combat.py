"""异步战斗模块自检。

不依赖 ok-script（那是外部包，开发机上没装），
只验证框架里"纯逻辑"的部分：时间表推算、参数校验、状态跳转。

跑法：
    python tools/selfcheck_async_combat.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_module(rel_path: str, name: str):
    """按文件路径加载模块，绕开包的 __init__（那个会 import ok-script）。"""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


T = load_module('src/combat/async_combat/types.py', 'ac_types')

failures: list[str] = []


def check(label: str, actual, expected) -> None:
    ok = actual == expected
    print(f'  [{"OK" if ok else "FAIL"}] {label}: {actual!r}' + ('' if ok else f' (期望 {expected!r})'))
    if not ok:
        failures.append(label)


print('=' * 60)
print('1. 动作与校验点的时间表推算')
print('=' * 60)

step = T.BlindStep(
    name='测试段',
    actions=[
        T.Action(t=0.0, kind=T.ActionKind.CLICK),
        T.Action(t=0.15, kind=T.ActionKind.CLICK),
        T.Action(t=0.30, kind=T.ActionKind.HOLD_START),
        T.Action(t=1.50, kind=T.ActionKind.HOLD_END),
    ],
    checks=[T.Check(t=1.80, name='hsin_lib1')],
)
check('理论时长取动作与校验的最大值', step.duration, 1.80)
check('上限自动加 0.5 秒余量', step.max_duration, 2.30)

print()
print('=' * 60)
print('2. 乱序录入会被自动排序')
print('=' * 60)

messy = T.BlindStep(
    name='乱序段',
    actions=[T.Action(t=0.30), T.Action(t=0.0), T.Action(t=0.15)],
    checks=[T.Check(t=0.9, name='x'), T.Check(t=0.4, name='y')],
)
check('动作已排序', [a.t for a in messy.actions], [0.0, 0.15, 0.30])
check('校验点已排序', [c.t for c in messy.checks], [0.4, 0.9])

print()
print('=' * 60)
print('3. 非法参数会被拦下')
print('=' * 60)

for label, factory in [
    ('负时间动作', lambda: T.Action(t=-1.0)),
    ('负时间校验', lambda: T.Check(t=-0.5, name='x')),
    ('容错次数为 0', lambda: T.Check(t=0.0, name='x', tolerance=0)),
]:
    try:
        factory()
        check(label + ' 应当抛错', '没抛错', '抛 ValueError')
    except ValueError as exc:
        check(label, 'ValueError', 'ValueError')
        print(f'         └ {exc}')

print()
print('=' * 60)
print('4. 状态跳转（run_cycle 的核心规则）')
print('=' * 60)

# 用一个极简的假引擎复现 run_cycle 的跳转规则
class FakeResult:
    def __init__(self, value): self.value = value

def simulate_cycle(steps, start, results, max_steps=40):
    """照着 engine.run_cycle 的规则跑一遍，返回访问过的状态序列。"""
    state, visited = start, []
    for _ in range(max_steps):
        step = steps.get(state)
        if step is None:
            break
        visited.append(state)
        ok = results.get(state, True)
        nxt = step.on_success if ok else step.on_fail
        if not nxt or nxt not in steps:
            break
        state = nxt
    return visited

steps = {
    'A': T.BlindStep(name='A', on_success='B', on_fail='A'),
    'B': T.BlindStep(name='B', on_success='C', on_fail='A'),
    'C': T.BlindStep(name='C', on_success='A', on_fail='A'),
}
check('全成功时 A→B→C→A→B→C→A', simulate_cycle(steps, 'A', {}, max_steps=7),
      ['A', 'B', 'C', 'A', 'B', 'C', 'A'])
check('B 失败则回到 A', simulate_cycle(steps, 'A', {'B': False}, max_steps=3), ['A', 'B', 'A'])
check('起始状态不存在时立刻停下', simulate_cycle(steps, 'Z', {}), [])

print()
print('=' * 60)
print('5. 心的循环拼装')
print('=' * 60)

# 铺一条包链 + 假 logger。
# hsin_steps 会 import src 包和 ok.util.logger，这两样裸环境下都没有；
# 手工补上，顺便避免执行到 __init__.py（那个会拉 ok-script）。
import types as pytypes
for _pkg in ('src', 'src.combat', 'src.combat.async_combat'):
    if _pkg not in sys.modules:
        _m = pytypes.ModuleType(_pkg)
        _m.__path__ = [str(ROOT / _pkg.replace('.', '/'))]
        sys.modules[_pkg] = _m
sys.modules['src.combat.async_combat.types'] = T

fake_ok = pytypes.ModuleType('ok')
fake_logger_mod = pytypes.ModuleType('ok.util.logger')
class _FakeLogger:
    def info(self, *a, **k): pass
    def debug(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
fake_logger_mod.Logger = type('L', (), {'get_logger': staticmethod(lambda name: _FakeLogger())})
fake_ok.util = pytypes.ModuleType('ok.util')
sys.modules['ok'] = fake_ok
sys.modules['ok.util'] = fake_ok.util
sys.modules['ok.util.logger'] = fake_logger_mod

H = load_module('src/combat/async_combat/hsin_steps.py', 'ac_hsin')
cycle = H.build_hsin_cycle()
check('心的循环段数', len(cycle), 5)
check('所有 on_success 都指向存在的状态',
      sorted({s.on_success for s in cycle.values() if s.on_success} - set(cycle)), [])
check('所有 on_fail 都指向存在的状态',
      sorted({s.on_fail for s in cycle.values() if s.on_fail} - set(cycle)), [])

print()
print('每段的动作数和时长：')
for name, s in cycle.items():
    print(f'  {name:<24} {len(s.actions):>2} 动作 / {len(s.checks)} 校验 / {s.duration:.2f}s')

print()
print('=' * 60)
if failures:
    print(f'❌ 有 {len(failures)} 项没过: {failures}')
    sys.exit(1)
print('✅ 自检全部通过')