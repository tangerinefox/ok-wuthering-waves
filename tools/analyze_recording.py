"""录制分析器：把原始键鼠事件整理成能看的动作序列和时序数据。

它干三件事
----------
1. 配对：把 mouse_down / mouse_up 配对成"点击"或"长按"
2. 分段：按间隔把动作切成一簇一簇的，一簇就是一段连招
3. 量数：算出每段里"连续点击的间隔"是多少，好填进盲打段

怎么用
------
    python tools/analyze_recording.py recordings/record-20261007-213000.json
    python tools/analyze_recording.py <文件> --gap 1.5      # 改分段阈值
    python tools/analyze_recording.py <文件> --json out.json # 顺便导出机器可读的段草稿

判读要点
--------
- 人打游戏时会疯狂连点，所以"点击"的数量会远多于"有效动作"。
  分析器不猜你哪一下是认真的，它只如实列出间隔——**挑哪几下由你决定**。
- 长按（hold）通常是有意义的输入，鼠标按住不放超过 hold 阈值就算。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

#: 鼠标按住超过这个秒数，算"长按"而不是"点击"
HOLD_THRESHOLD = 0.30
#: 两个动作间隔超过这个秒数，算"新的一段"
DEFAULT_GAP = 1.5
#: 间隔小于这个秒数的连续点击，视为手抖重复触发，合并掉
DEFAULT_MERGE = 0.08


# ---------------------------------------------------------------------- #
# 整理
# ---------------------------------------------------------------------- #

def pair_mouse(events: list[dict], hold_threshold: float) -> list[dict]:
    """把按下/松开配成动作。没配上的按下（比如录到一半停了）会被丢掉。"""
    pending: dict[str, dict] = {}
    actions: list[dict] = []

    for event in events:
        kind = event['type']
        if kind == 'mouse_down':
            pending[event['button']] = event
        elif kind == 'mouse_up':
            down = pending.pop(event['button'], None)
            if down is None:
                continue
            duration = event['t'] - down['t']
            actions.append({
                't': round(down['t'], 4),
                'kind': 'hold' if duration >= hold_threshold else 'click',
                'source': 'mouse',
                'key': event['button'],
                'dur': round(duration, 4),
            })

    return actions


def collect_keys(events: list[dict]) -> list[dict]:
    """键盘只取按下事件。

    键盘的长按在游戏里基本没有意义（连招都是点按），所以不做配对，
    免得"按着 W 跑图"产生一堆噪音。
    """
    return [
        {'t': round(e['t'], 4), 'kind': 'key', 'source': 'key',
         'key': e['key'], 'dur': 0.0}
        for e in events if e['type'] == 'key_down'
    ]


def merge_actions(events: list[dict], hold_threshold: float) -> list[dict]:
    actions = pair_mouse(events, hold_threshold) + collect_keys(events)
    actions.sort(key=lambda a: a['t'])
    return actions


def debounce(actions: list[dict], threshold: float) -> list[dict]:
    """去掉"手抖连点"。

    人紧张时会在同一下普攻上狂按好几下，间隔只有几十毫秒。这种重复触发
    对连招没有意义，还会把"有效间隔"的统计整个带偏——所以先合并再量。

    规则：间隔小于阈值的连续 click，只留第一下。
    长按和按键不参与合并——它们的意图是明确的，不该被吞掉。
    """
    if threshold <= 0:
        return actions

    merged: list[dict] = []
    for action in actions:
        if (merged
                and action['kind'] == 'click'
                and merged[-1]['kind'] == 'click'
                and action['t'] - merged[-1]['t'] < threshold):
            continue
        merged.append(action)
    return merged


def split_segments(actions: list[dict], gap: float) -> list[list[dict]]:
    """按间隔切段。"""
    if not actions:
        return []
    segments: list[list[dict]] = [[actions[0]]]
    for prev, cur in zip(actions, actions[1:]):
        if cur['t'] - prev['t'] > gap:
            segments.append([])
        segments[-1].append(cur)
    return segments


# ---------------------------------------------------------------------- #
# 度量
# ---------------------------------------------------------------------- #

def click_intervals(actions: list[dict]) -> list[float]:
    """连续点击之间的间隔（只看 click，hold 和 key 会打断连续性）。"""
    intervals: list[float] = []
    run: list[dict] = []
    for action in actions:
        if action['kind'] == 'click':
            run.append(action)
        else:
            intervals.extend(_gaps(run))
            run = []
    intervals.extend(_gaps(run))
    return [round(i, 4) for i in intervals]


def _gaps(run: list[dict]) -> list[float]:
    if len(run) < 2:
        return []
    return [b['t'] - a['t'] for a, b in zip(run, run[1:])]


def run_lengths(actions: list[dict]) -> list[tuple[int, float]]:
    """连续点击的"连击长度"分布：[(几下, 这段用了多久), ...]"""
    runs: list[tuple[int, float]] = []
    streak: list[dict] = []
    for action in actions:
        if action['kind'] == 'click':
            streak.append(action)
        else:
            if len(streak) >= 2:
                runs.append((len(streak), round(streak[-1]['t'] - streak[0]['t'], 4)))
            streak = []
    if len(streak) >= 2:
        runs.append((len(streak), round(streak[-1]['t'] - streak[0]['t'], 4)))
    return runs


# ---------------------------------------------------------------------- #
# 输出
# ---------------------------------------------------------------------- #

def fmt_action(action: dict) -> str:
    if action['kind'] == 'click':
        return f"{action['t']:7.3f}  点击  {action['key']}"
    if action['kind'] == 'hold':
        return f"{action['t']:7.3f}  长按  {action['key']}  持续 {action['dur']:.3f}s"
    return f"{action['t']:7.3f}  按键  {action['key'].upper()}"


def report(payload: dict, actions: list[dict], segments: list[list[dict]], gap: float,
           raw_count: int | None = None) -> None:
    print('=' * 62)
    print(f"录制时间：{payload.get('recorded_at', '?')}")
    print(f"总时长：{payload.get('duration', 0):.1f}s ｜ 原始事件 {payload.get('event_count', 0)} 个")
    merged_note = ''
    if raw_count is not None and raw_count != len(actions):
        merged_note = f'（合并掉 {raw_count - len(actions)} 个手抖连点）'
    print(f"整理后：{len(actions)} 个动作 {merged_note} ｜ 按 {gap}s 间隔切成 {len(segments)} 段")
    print('=' * 62)

    for idx, segment in enumerate(segments, 1):
        span = segment[-1]['t'] - segment[0]['t']
        clicks = sum(1 for a in segment if a['kind'] == 'click')
        holds = sum(1 for a in segment if a['kind'] == 'hold')
        keys = [a['key'].upper() for a in segment if a['kind'] == 'key']
        tag = f'点击×{clicks}'
        if holds:
            tag += f' 长按×{holds}'
        if keys:
            tag += f' 按键[{" ".join(keys)}]'

        print(f'\n【第 {idx} 段】{segment[0]["t"]:.2f}s → {segment[-1]["t"]:.2f}s  '
              f'(跨度 {span:.2f}s)  {tag}')
        for action in segment:
            print('   ' + fmt_action(action))

    print()
    print('=' * 62)
    print('时序统计')
    print('=' * 62)

    intervals = click_intervals(actions)
    if intervals:
        print(f'连续点击间隔  样本 {len(intervals)} 个')
        print(f'  中位数 {statistics.median(intervals):.3f}s')
        print(f'  最小   {min(intervals):.3f}s')
        print(f'  最大   {max(intervals):.3f}s')
    else:
        print('没有连续点击（可能全是单点或长按）')

    runs = run_lengths(actions)
    if runs:
        print()
        print('连击长度分布（连续点击几下 / 每段用了多久）')
        for count, span in runs:
            avg = span / (count - 1) if count > 1 else 0
            print(f'  连 {count:>2} 下   跨度 {span:.3f}s   平均间隔 {avg:.3f}s')

    holds = [a for a in actions if a['kind'] == 'hold']
    if holds:
        print()
        print('长按明细')
        for action in holds:
            print(f"  {action['t']:7.3f}s  持续 {action['dur']:.3f}s")

    keys = [a for a in actions if a['kind'] == 'key']
    if keys:
        print()
        print('按键明细')
        for action in keys:
            print(f"  {action['t']:7.3f}s  {action['key'].upper()}")


def draft_cycles(segments: list[list[dict]]) -> list[dict]:
    """把段转成"盲打段草稿"，格式跟 hsin_steps.py 里的对得上。"""
    drafts = []
    for idx, segment in enumerate(segments, 1):
        base = segment[0]['t']
        drafts.append({
            'name': f'录制段{idx}',
            'duration': round(segment[-1]['t'] - base, 4),
            'actions': [
                {
                    't': round(a['t'] - base, 4),
                    'kind': a['kind'],
                    'key': a['key'],
                    'dur': a['dur'],
                }
                for a in segment
            ],
        })
    return drafts


# ---------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description='分析输入录制文件')
    parser.add_argument('path', help='recordings/ 下的 json 文件')
    parser.add_argument('--gap', type=float, default=DEFAULT_GAP,
                        help=f'分段间隔阈值（秒），默认 {DEFAULT_GAP}')
    parser.add_argument('--hold', type=float, default=HOLD_THRESHOLD,
                        help=f'长按判定阈值（秒），默认 {HOLD_THRESHOLD}')
    parser.add_argument('--merge', type=float, default=DEFAULT_MERGE,
                        help=f'手抖连点合并阈值（秒），设 0 关闭。默认 {DEFAULT_MERGE}')
    parser.add_argument('--json', dest='json_out', default=None,
                        help='把段草稿导出成 json')
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f'文件不存在：{path}')
        sys.exit(1)

    payload = json.loads(path.read_text(encoding='utf-8'))
    raw_actions = merge_actions(payload['events'], args.hold)
    actions = debounce(raw_actions, args.merge)
    segments = split_segments(actions, args.gap)

    report(payload, actions, segments, args.gap, raw_count=len(raw_actions))

    if args.json_out:
        drafts = draft_cycles(segments)
        Path(args.json_out).write_text(
            json.dumps(drafts, ensure_ascii=False, indent=1), encoding='utf-8')
        print()
        print(f'段草稿已导出：{args.json_out}')


if __name__ == '__main__':
    main()