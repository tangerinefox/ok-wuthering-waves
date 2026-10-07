"""生成一份样例录制，用来验证分析器。

不需要真的按键盘——这里按"心的循环"的样子造一份假数据，
包括人打游戏时那种紧张连点（噪音），看看分析器能不能正确整理。

跑法：
    python tools/make_sample_recording.py
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / 'recordings' / 'sample.json'
random.seed(20261007)          # 固定种子，结果可复现


def build_events() -> list[dict]:
    events: list[dict] = []
    mouse_pos = (1000, 600)

    def click(t: float) -> None:
        events.append({'t': round(t, 4), 'type': 'mouse_down', 'button': 'left',
                       'x': mouse_pos[0], 'y': mouse_pos[1]})
        events.append({'t': round(t + 0.025, 4), 'type': 'mouse_up', 'button': 'left',
                       'x': mouse_pos[0], 'y': mouse_pos[1]})

    def press_and_hold(t: float, duration: float) -> None:
        events.append({'t': round(t, 4), 'type': 'mouse_down', 'button': 'left',
                       'x': mouse_pos[0], 'y': mouse_pos[1]})
        events.append({'t': round(t + duration, 4), 'type': 'mouse_up', 'button': 'left',
                       'x': mouse_pos[0], 'y': mouse_pos[1]})

    def key(t: float, name: str) -> None:
        events.append({'t': round(t, 4), 'type': 'key_down', 'key': name})
        events.append({'t': round(t + 0.03, 4), 'type': 'key_up', 'key': name})

    def nervous_clicks(base: float, count: int) -> None:
        """人紧张时会多点的几下，间隔很碎。"""
        for i in range(count):
            click(base + 0.035 * (i + 1))

    # ---- 段 1：红心攒心 → 强化重击 ----
    for i, base in enumerate((0.00, 0.15, 0.30)):
        click(base)
        nervous_clicks(base, random.randint(1, 2))       # 噪音
    press_and_hold(0.62, 1.18)                            # 强化重击

    # ---- 段 2：按 R 切形态 ----
    key(2.35, 'r')

    # ---- 段 3：黄心攒心 → 按 E ----
    for base in (3.90, 4.06, 4.22):
        click(base)
        nervous_clicks(base, random.randint(1, 2))
    key(4.42, 'e')

    # ---- 段 4：13 秒窗口烧心 → 强化重击 ----
    for base in (6.10, 6.26, 6.42, 6.58):
        click(base)
        nervous_clicks(base, random.randint(1, 3))
    press_and_hold(6.95, 1.22)

    # ---- 段 5：放大招 ----
    key(9.05, 'r')

    return events


def main() -> None:
    events = build_events()
    events.sort(key=lambda e: e['t'])
    payload = {
        'recorded_at': datetime.now().isoformat(timespec='seconds'),
        'note': '样例数据（模拟心的循环，含连点噪音）',
        'duration': events[-1]['t'],
        'event_count': len(events),
        'events': events,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'样例已生成：{OUT}')
    print(f'  {len(events)} 个原始事件，时长 {payload["duration"]:.2f}s')


if __name__ == '__main__':
    main()