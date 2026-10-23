# AGENTS.md — 抖音极速版领金币自动化

给接手这个项目的 agent / 开发者看的入口文档。**先读这一页，再改代码。**

一句话：用 ADB + 截图像素判据，在 Android 真机上自动完成抖音极速版（`com.ss.android.ugc.aweme.lite`）的
「看视频 / 看广告 / 逛街」赚金币任务并自动领奖，循环执行。

---

## 0. 每个会话第一件事：同步版本（所有 agent 必做）

本项目在真机上持续迭代，新版本经常修旧 bug。**开新会话、动手之前先跑：**

```bash
python update.py            # 落后上游就 git pull --ff-only，并打印新版本
python update.py --check    # 只看有没有新版，不拉
```

- 输出第一行是 `VERSION` + commit。**回答用户时先报这个版本号**，让用户知道你基于哪一版工作。
- 如果提示「已更新」，且 `dy_coin.py` / `dy_auto.py` 有变化：正在跑的脚本仍是旧代码，
  需要按 §1 停掉再启动。
- `git fetch` 失败（离线）不影响本地运行，继续用当前版本并在回答里说明「离线，未同步」。
- 改完代码要**升 `VERSION`**（纯修复 +0.0.1，新功能 +0.1.0），然后 commit + push，
  否则别的 agent 的会话拉不到你的修复。

---

## 1. 三步跑起来

```bash
pip install -r requirements.txt      # numpy + Pillow
python check.py                      # 环境自检，逐项告诉你哪儿不对
python dy_coin.py                    # 开跑（无限循环）
```

`python dy_coin.py 3` 只跑 3 轮；`python dy_coin.py 3 600` 是 3 轮、每轮看视频 600 秒。
停止：在脚本目录建空文件 `coin.stop`（Windows 下 `New-Item coin.stop`）。

**前置条件**：手机开 USB 调试（或无线调试并 `adb connect`），`adb devices` 能看到 `device` 状态；
屏幕分辨率必须是 **1080×2400**（否则坐标全错，`check.py` 会拦）。

---

## 2. 环境变量（换机器只改这里，不改代码）

| 变量 | 作用 | 默认 |
|---|---|---|
| `ADB_BIN` | adb 可执行文件路径 | 先找 PATH，再找常见安装目录 |
| `ADB_SERIAL` / `ANDROID_SERIAL` | 指定设备序列号 | 自动选唯一在线设备，多设备时优先 USB |
| `DY_DEV_HOST` | 无线调试手机 IP（可选兜底，正常靠 mdns 自动发现） | 空 |
| `DY_DEV_ADDR` | 无线调试 `IP:PORT`（可选兜底；端口会漂移，一般别写死） | 空 |
| `DYAUTO_TMP` | 截图/日志临时目录 | 脚本所在目录 |
| `FORCE_RES` | 设任意值可跳过分辨率检查 | 未设 |

adb 路径和设备序列号由 `dy_env.py` 统一解析，`dy_coin.py` / `dy_auto.py` 都从这里取。

---

## 3. 文件

| 文件 | 作用 | 要不要改 |
|---|---|---|
| `dy_coin.py` | 主脚本：调度 / 广告 / 逛街 / 领奖 / 退出 | 改业务逻辑改这里 |
| `dy_auto.py` | 拟人化看视频引擎，`dy_coin` 以模块调用 | 一般不动 |
| `dy_env.py` | adb 路径、设备选择、分辨率探测 | 换环境先看这里 |
| `check.py` | 环境自检 | 部署时跑 |
| `update.py` | 版本自检 + `git pull` | **每次会话先跑** |
| `VERSION` | 语义化版本号 | 改了行为就升 |
| `README.md` | **完整技术文档**：坐标表、像素判据、踩坑记录 | 改代码前必读 |
| `requirements.txt` | Python 依赖 | — |
| `coin.log` | 运行日志（UTF-8，追加写） | — |
| `coin.stop` | 存在即退出 | 停止用 |
| `coin.lock` | 单实例锁（存 PID） | 不要手删在跑的锁 |

---

## 4. 工作原理（一句话）

**没有 OCR，也不用 `uiautomator`**（任务页有秒级倒计时，`dump` 永远等不到 idle）。
全部靠 **固定坐标点击 + 截图像素判据**识别页面，用 `dumpsys window` 的 `mCurrentFocus`
里的 Activity 名区分广告页 / 逛街页 / feed。

| 页面 | 判据 |
|---|---|
| feed | `(540,2310)` 亮红（赚钱按钮） |
| 任务页 | `(540,350)` 粉红 `(255,79,116)` 且 `(540,2310)` 白 |
| 弹窗打开 | 四角变暗 + 白卡内找到按钮（`is_dialog_open`） |
| 广告页 | Activity 含 `excitingvideo` |
| 逛街页 | Activity 含 `bulletcontainer` |

---

## 5. 业务规则（改逻辑前必须知道）

1. **视频优先**：里程档位金币 ≥1000 就继续刷视频，<1000 才转去看广告。
2. **逛街冷却 → 立刻转广告**：点逛街进不去 H5 页（不跳 `BulletContainerActivity`）就是冷却；
   立刻退出，去做广告，做完回来再试。
3. **连续 2 轮没有任何可做任务 → 退出抖音**（`force-stop` + 回桌面，脚本结束）。
4. 里程格只点「**待领取**」（鲜红字 + 白底），不碰「已领取」（淡粉）/「赚钱中」（红底白字）。
5. 广告弹窗有**两种状态，别搞混**：
   - 等待态：标题「再看 N 秒可领奖励」，主按钮 `继续观看`，下方两段文字 → **点继续观看等倒计时**
   - 完成态：标题「再看一个视频额外获得 +N 金币」，主按钮 `领取奖励` → **点坚持退出**
   判据在 `ad_waiting()`：数药丸下方文字行的段数，2 段 = 等待态。
6. 广告页右上角 `×` **点不动**（实测点击后截图字节不变），必须点 `坚持退出`。
7. 逛街 H5 页「再看 N 秒」弹窗**只点一次**，反复点会不断重新弹；滑动间隔 4.5~7 秒，
   太频繁会打断计时。
8. 刷视频必须**真的上滑换视频**才累计时长，静置几乎不计时。

---

## 6. 改代码的约束

- **所有坐标是 1080×2400 绝对值**。换分辨率必须重新标定 `XY_*`、`MILE_BOXES`、
  `MILE_BG_Y`、各弹窗 y 区间。
- 判断页面状态**不要用 `uiautomator dump`**，会卡死；用 `shot()` + numpy 像素判据。
- 新增任务动作前先看 `escape_to_task()`：它负责从任何未知页面回到任务页，
  是最容易踩坑的地方（误入提现页很危险）。
- 改完跑 `python -m py_compile dy_coin.py dy_auto.py dy_env.py`，再用真机跑一轮验证。
- 日志一律走 `log()`（同时进 `coin.log` 和 stdout），别用裸 `print`。

---

## 7. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动就退，日志「已有实例在运行」 | 上次没清锁 | 确认无 python 进程后删 `coin.lock` |
| 设备一直「等待中」 | USB 掉线 / 无线端口漂移 | `adb devices` → `adb connect IP:PORT` |
| 点哪儿都没反应 | 分辨率变了 | `python check.py` 看分辨率 |
| 停在广告页反复点 | 等待态被当完成态 | 检查 `ad_waiting()` 判据 |
| 任务页找不到广告/逛街行 | 页面滚动了 | `find_task_row()` 动态定位，别写死 y |

---

## 8. 脚本退出条件

- 建了 `coin.stop`
- 命令行指定的轮数跑完
- 连续 `IDLE_LIMIT`（默认 2）轮没有任何可做任务 → 自动退出抖音并结束

---

## 9. 给其他 AI agent 的接手清单（Claude Code / Codex / Cursor / 任意 agent）

### 9.1 这个仓库是什么

一个**在 Android 真机上自动做抖音极速版赚金币任务**的 Python 脚本集：连续下滑刷视频、
看广告、进「逛街」H5 页、自动领里程档位奖励。识别全部基于 **ADB 截图 + numpy 像素判据**，
不使用 OCR、不使用 `uiautomator`。

### 9.2 首次接手（只做一次）

```bash
git clone https://github.com/xyw823171794/douyin-coin.git
cd douyin-coin
pip install -r requirements.txt
python check.py        # 必须全绿：adb 可用、设备在线、分辨率 1080x2400
```

`check.py` 报错就先修环境，不要直接跑主脚本——脚本会盲点坐标，环境不对可能点到提现页。

### 9.3 每个会话的标准流程

1. `python update.py` —— 拉最新版，**在回答开头报出 VERSION + commit**。
2. 如果脚本正在跑，先看 `coin.log` 末尾 40 行，确认它现在处在什么分支（看视频 / 广告 / 逛街 / 领奖）。
3. 改代码：只改 `dy_coin.py`（调度、广告、逛街、领奖）或 `dy_auto.py`（看视频引擎）。
4. `python -m py_compile dy_coin.py dy_auto.py dy_env.py update.py`
5. 真机验证：抓一张 `adb -s <serial> exec-out screencap -p > now.png`，用新判据跑一遍，
   确认 False/True 符合预期，**不要只看代码觉得对**。
6. 升 `VERSION` → `git add -A && git commit -m "..." && git push`。

### 9.4 本机（Windows）的运行方式

后台常驻启动（避免终端一关就停）：

```powershell
Start-Process -FilePath python.exe -ArgumentList 'dy_coin.py' `
  -WorkingDirectory 'C:\path\to\douyin-coin' -WindowStyle Hidden
```

重启前必须：`taskkill /F /IM python.exe` 然后删 `coin.lock`（单实例锁存 PID）。

### 9.5 硬性约束（踩过的坑，别重犯）

- **不要用 `uiautomator dump` 判页面**：任务页有秒级倒计时，dump 永远等不到 idle，会卡死整轮。
- **不要凭感觉调坐标**：一切坐标都是 1080×2400 绝对值，改判据前先用真机截图验证像素值。
- **不要看到「不在预期页面」就连按 BACK**：会把 App 退到桌面，反而更慢；正确做法是判
  `PKG not in focus()` → `relaunch()`。
- **改判据必须留注释说明依据**（哪张截图、什么比例、为什么是这个阈值），否则下一个人会改回去。
- 提交前确认没有把 `coin.log`、`*.png`、`coin.lock` 加进仓库（`.gitignore` 已覆盖，别用 `git add -f`）。

### 9.6 和用户沟通

- 用中文回答；先说结论，再给证据（日志行、像素比例）。
- 用户是在**手机旁边看着屏幕**给你反馈的（"明明已经待领取了"），他的观察优先于日志。
- 报版本号：`v1.0.0 (abc1234)`，方便双方确认跑的是同一份代码。
