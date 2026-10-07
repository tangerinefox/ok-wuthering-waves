"""异步战斗引擎：把"敲键"和"看屏幕"拆成两个线程。

为什么这么做
------------
老实现是串行的：敲一下 → 看一眼 → 再敲一下。敲键本身只要几毫秒，
但"看一眼"（截图 + 模板匹配）要几十毫秒，于是整个循环被"看"拖着走。

这里改成：输入线程按时间表闷头敲，感知线程在旁边慢慢看。
两者只通过两个变量通信（是否中止 / 连续几次不符），互不阻塞。

安全边界
--------
输入线程**直接操作交互层**（executor.interaction），不碰 task.click()。
因为 task.click() 里夹着 UI 信号发射和场景重置，跨线程调用有隐患。
感知线程**自己抓帧**，不跟执行器的帧循环抢。
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

from ok.util.logger import Logger

from src.combat.async_combat.types import (
    Action,
    ActionKind,
    Check,
    EngineStats,
    StepResult,
    BlindStep,
)

logger = Logger.get_logger(__name__)


class Verifier(Protocol):
    """校验器接口。engine 不关心你怎么判断，只关心"是/否"。"""

    def verify(self, check: Check) -> bool:
        """返回 True 表示画面符合预期。"""
        ...

    def warm_up(self) -> None:
        """可选的预热（比如建立抓帧通道）。"""
        ...


class AsyncCombatEngine:
    """双线程执行器。

    用法::

        engine = AsyncCombatEngine(task, verifier)
        result = engine.run_step(step)
        if result is not StepResult.OK:
            ...  # 按 step.on_fail 决定退回哪里

    一个 engine 实例可以反复 run_step，不要跨角色复用。
    """

    #: 输入线程的轮询粒度。1ms 足够精确，又不至于把 CPU 烧了。
    TICK = 0.001
    #: 段跑完后，最多再等多久收尾（防止线程卡死）
    JOIN_SLACK = 1.0

    def __init__(self, task, verifier: Verifier, *, center_click: bool = True):
        """
        :param task: ok-ww 的 task 对象（用来拿屏幕尺寸、交互层）
        :param verifier: 校验器
        :param center_click: 鼠标点击是否默认落在屏幕中心（鸣潮普攻区）
        """
        self.task = task
        self.executor = task.executor
        self.interaction = task.executor.interaction
        self.verifier = verifier
        self.center_click = center_click

        # ---- 跨线程共享状态 ----
        self._lock = threading.Lock()
        self._abort = False
        self._abort_reason = ''
        self._consecutive_miss = 0

        # ---- 运行时状态 ----
        self._held_keys: set[str] = set()   # 还按着没松的键，中止时要补松
        self._held_lock = threading.Lock()

        self.stats = EngineStats()

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #

    def run_step(self, step: BlindStep) -> StepResult:
        """跑完一整段盲打，返回结果。

        正常返回三种：OK / ABORTED / TIMEOUT。
        无论哪种，函数返回时**保证所有按下的键都已松开**。
        """
        self._reset_state()
        self.stats.steps_run += 1

        try:
            self.verifier.warm_up()
        except Exception as exc:  # 预热失败不该阻断战斗
            logger.warning(f'校验器预热失败，继续: {exc}')

        input_thread = threading.Thread(
            target=self._run_input, args=(step,), name=f'async-input-{step.name}', daemon=True)
        sense_thread = threading.Thread(
            target=self._run_sense, args=(step,), name=f'async-sense-{step.name}', daemon=True)

        logger.info(f'异步段开始: {step.describe()}')
        started = time.perf_counter()
        input_thread.start()
        sense_thread.start()

        # 输入线程是主时钟：它跑完（或被中止）就算这一段结束
        input_thread.join(timeout=step.max_duration + self.JOIN_SLACK)
        elapsed = time.perf_counter() - started

        if input_thread.is_alive():
            self._abort_now('输入线程超时未结束')
            input_thread.join(timeout=self.JOIN_SLACK)

        # 感知线程通常晚于输入线程结束（它可能正在做一次昂贵的匹配）
        sense_thread.join(timeout=self.JOIN_SLACK)
        if sense_thread.is_alive():
            logger.warning(f'感知线程未及时退出: {step.name}')

        self._release_all_held()

        with self._lock:
            aborted, reason = self._abort, self._abort_reason

        if aborted:
            self.stats.steps_aborted += 1
            self.stats.abort_reasons.append(reason)
            logger.info(f'异步段中断: {step.name} —— {reason} (用时 {elapsed:.3f}s)')
            return StepResult.ABORTED

        if elapsed >= step.max_duration:
            self.stats.steps_timeout += 1
            logger.info(f'异步段超时: {step.name} (用时 {elapsed:.3f}s / 上限 {step.max_duration:.2f}s)')
            return StepResult.TIMEOUT

        self.stats.steps_ok += 1
        logger.info(f'异步段完成: {step.name} (用时 {elapsed:.3f}s)')
        return StepResult.OK

    def run_cycle(self, steps: dict[str, BlindStep], start: str, max_steps: int = 40) -> EngineStats:
        """按状态表把若干段串起来跑，直到没有下一段可去。

        跳转规则很简单：这一段跑成功就去 on_success，失败或超时就去 on_fail。
        两张表都没写（或指向不存在的状态）就停下来。

        :param steps: 状态名 → 段，通常由 build_xxx_cycle() 生成
        :param start: 从哪个状态开始
        :param max_steps: 最多跑多少段，防止两边表写岔了无限转
        :return: 本次累计的统计
        """
        state = start
        visited = 0
        while visited < max_steps:
            step = steps.get(state)
            if step is None:
                logger.warning(f'状态 {state!r} 不在表里，循环到此为止')
                break

            visited += 1
            result = self.run_step(step)

            if result is StepResult.OK:
                nxt = step.on_success
            else:
                nxt = step.on_fail

            if not nxt:
                logger.info(f'段 {step.name} 结束于 {result.value}，且没有指定下一段')
                break
            if nxt not in steps:
                logger.warning(f'段 {step.name} 指向了不存在的状态 {nxt!r}')
                break

            logger.debug(f'{step.name} [{result.value}] → {nxt}')
            state = nxt

        logger.info(f'本轮循环结束: {self.stats.summary()}')
        return self.stats

    def abort(self) -> None:
        """从外部中止当前段（比如外面发现了战斗结束）。"""
        self._abort_now('外部请求中止')

    # ------------------------------------------------------------------ #
    # 输入线程
    # ------------------------------------------------------------------ #

    def _run_input(self, step: BlindStep) -> None:
        """按时间表闷头敲。除了"要不要中止"，它什么都不看。"""
        started = time.perf_counter()
        try:
            for action in step.actions:
                if not self._sleep_until(started + action.t):
                    logger.debug(f'输入线程在 {action.t:.3f}s 处收到中止信号')
                    return
                self._do_action(action)
                self.stats.total_actions += 1
        except Exception as exc:
            logger.error(f'输入线程异常: {exc}', exc_info=True)
            self._abort_now(f'输入异常: {exc}')

    def _sleep_until(self, deadline: float) -> bool:
        """睡到指定时刻。返回 False 表示期间被中止。

        用短轮询而不是一次 sleep 到底，是为了中止时能立刻响应——
        被控了还继续敲完剩下的键，只会把后面搅得更乱。
        """
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            if self._is_aborted():
                return False
            time.sleep(min(remaining, self.TICK))
        return not self._is_aborted()

    def _do_action(self, action: Action) -> None:
        """把一次动作翻译成底层的输入调用。"""
        x, y = self._click_point()

        if action.kind is ActionKind.CLICK:
            self.interaction.click(x, y, down_time=action.down_time, key=action.key)
        elif action.kind is ActionKind.KEY:
            self.interaction.send_key(action.key, action.down_time)
        elif action.kind is ActionKind.HOLD_START:
            self.interaction.mouse_down(x, y, key=action.key)
            with self._held_lock:
                self._held_keys.add(action.key)
        elif action.kind is ActionKind.HOLD_END:
            self.interaction.mouse_up(key=action.key)
            with self._held_lock:
                self._held_keys.discard(action.key)
        else:  # pragma: no cover - 枚举已穷尽
            raise ValueError(f'未知动作类型: {action.kind}')

        if action.note:
            logger.debug(f'  ↳ {action.t:.3f}s {action.kind.value} {action.key} ({action.note})')

    # ------------------------------------------------------------------ #
    # 感知线程
    # ------------------------------------------------------------------ #

    def _run_sense(self, step: BlindStep) -> None:
        """按校验点看屏幕。它落后于输入线程是正常的，不需要追。"""
        if not step.checks:
            return

        started = time.perf_counter()
        try:
            for check in step.checks:
                if not self._sleep_until(started + check.t):
                    return
                self._verify(check)
                if self._is_aborted():
                    return
        except Exception as exc:
            logger.error(f'感知线程异常: {exc}', exc_info=True)
            # 感知出问题不该直接判失败（可能是识别库的锅），只告警
            logger.warning('感知线程异常退出，本段将继续盲打到底')

    def _verify(self, check: Check) -> None:
        try:
            matched = bool(self.verifier.verify(check))
        except Exception as exc:
            logger.warning(f'校验 {check.name} 抛异常，本次视为通过: {exc}')
            matched = True

        if matched:
            with self._lock:
                self._consecutive_miss = 0
            return

        with self._lock:
            self._consecutive_miss += 1
            misses = self._consecutive_miss

        logger.debug(f'校验不符: {check.name} ({misses}/{check.tolerance})')
        if misses >= check.tolerance:
            self._abort_now(f'校验连续 {misses} 次不符: {check.name}')

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #

    def _click_point(self) -> tuple[int, int]:
        """点击落点。默认屏幕中心（鸣潮的普攻区）。"""
        if not self.center_click:
            return -1, -1
        return self.task.width_of_screen(0.5), self.task.height_of_screen(0.5)

    def _reset_state(self) -> None:
        with self._lock:
            self._abort = False
            self._abort_reason = ''
            self._consecutive_miss = 0

    def _is_aborted(self) -> bool:
        with self._lock:
            return self._abort

    def _abort_now(self, reason: str) -> None:
        with self._lock:
            if not self._abort:
                self._abort = True
                self._abort_reason = reason

    def _release_all_held(self) -> None:
        """兜底：把还按着的键松开。漏了这一步，游戏里鼠标会一直按着不放。"""
        with self._held_lock:
            pending = list(self._held_keys)
            self._held_keys.clear()
        for key in pending:
            try:
                self.interaction.mouse_up(key=key)
                logger.warning(f'补松开未释放的键: {key}')
            except Exception as exc:
                logger.error(f'补松开 {key} 失败: {exc}')