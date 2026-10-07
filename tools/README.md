# tools/ — 配套工具

都是独立脚本，**不依赖 ok-script 框架**，所以在这台没装框架、没装游戏的开发机上也能跑。

---

## 1. 输入录制器（图形界面）

```
python tools/input_recorder.py
```

点开后：

1. 点【开始录制】→ 3 秒倒计时（这几秒不录）
2. 窗口变成一条**置顶半透明悬浮条**：`● 录制中 / 已录时长 / 事件数`
3. 在游戏里正常打（Alt+Tab 切窗这类动作照录，不删）
4. 按 **F10** 结束并保存

存档在 `recordings/record-YYYYmmdd-HHMMSS.json`，**原汁原味，不做任何删减**。

**关于双击启动**：项目 `.gitignore` 里有 `*.bat`，所以启动用的 bat 没进版本库。
想双击的话，自己在 `tools/` 下建一个 `录制器.bat`：

```bat
@echo off
chcp 65001 >nul
cd /d "%~dp0.."
python tools\input_recorder.py
if errorlevel 1 pause
```

---

## 2. 录制分析器

```
python tools/analyze_recording.py recordings/record-xxx.json
```

干三件事：

- **配对**：`mouse_down` / `mouse_up` 配成"点击"或"长按"（超过 0.3 秒算长按）
- **去抖**：间隔小于 0.08 秒的连续点击只留第一下——**人紧张时的狂点会把间隔统计带偏，不去抖量出来的数是错的**
- **分段**：按间隔把动作切成一簇一簇，一簇就是一段连招

参数：

| 参数 | 默认 | 作用 |
|---|---|---|
| `--gap` | 1.5 | 两个动作间隔超过这个秒数就分段 |
| `--merge` | 0.08 | 手抖连点合并阈值，设 0 关闭 |
| `--hold` | 0.3 | 长按判定阈值 |
| `--json out.json` | — | 导出"段草稿"，格式跟 `hsin_steps.py` 对得上 |

---

## 3. 样例数据生成

```
python tools/make_sample_recording.py
```

造一份模拟"心的循环"的假录制（含手抖连点噪音），用来验证分析器。不需要真的按键。

---

## 4. 框架自检

```
python tools/selfcheck_async_combat.py
```

验证 `src/combat/async_combat/` 里"纯逻辑"的部分——时间表推算、参数校验、状态跳转。
不依赖 ok-script（用假 logger 顶替）。

**写完代码顺手跑一下**。上次就是它抓出两个 bug：`on_fail` 指向未定义的状态、
`on_success` 用了简称跟状态表对不上——这两个都是"能编译、一跑就死循环"的错。

---

## 完整工作流

```
在游戏里打一套
      ↓  input_recorder.py
record-xxx.json
      ↓  analyze_recording.py
每段的真实时长、每次点击的真实间隔
      ↓  填进
src/combat/async_combat/hsin_steps.py 的占位值
      ↓  AsyncCombatEngine
跑起来
```