# -*- coding: utf-8 -*-
"""抖音极速版「看视频 / 看广告 领金币」自动化 —— 先看视频, 再看广告。

流程(每轮):
  1. 进「赚钱任务」页
  2. 【看视频】点红色「去看视频」→ 看视频页面, 静置播放累计里程
  3. 回任务页, 点里程格领 3380 金币
  4. 【看广告】点「看广告赚金币」字样 → 激励视频页
       - 右上角倒计时「X秒后可领奖励 ×」→ 结束后弹出「再看一个视频额外获得+N金币」
       - 点「坚持退出」→ 回任务页弹「恭喜累计获得奖励」→ 点「评价并收下金币」
  5. 顺带: 开宝箱 / 签到
  6. 循环

用法:
  python dy_coin.py          # 一直跑
  python dy_coin.py 3        # 只跑 3 轮
  python dy_coin.py 3 600    # 3 轮, 每轮看视频 600 秒
停止: 在本目录下新建 coin.stop 文件
"""
import subprocess, time, io, os, sys, re, random, datetime

try:                        # Windows 控制台默认 GBK, 强制 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
from PIL import Image

import dy_env

ADB = dy_env.ADB
SERIAL = dy_env.SERIAL
SCREEN = (1080, 2400)      # 所有坐标按这个分辨率标定
TMP = os.path.dirname(os.path.abspath(__file__))
LOG = TMP + r"\coin.log"
DEBUG_SHOT = TMP + r"\mile_debug.png"
DEBUG_SHOP = TMP + r"\shop_cd.png"
_ad_dbg = [0]
STOP = TMP + r"\coin.stop"
RAW = TMP + r"\_coin_raw.png"
PKG = "com.ss.android.ugc.aweme.lite"

# ---------------------------------------------------------------- 坐标 (1080x2400)
XY_MAKE_MONEY = (540, 2310)   # feed 底部「赚钱」
XY_WATCH_VIDEO = (800, 660)   # 任务页「去看视频」
XY_AD_TEXT = (300, 2112)      # 任务页「看广告赚金币」标题(滚动后位置会变, 见 find_task_row)
XY_CHEST = (913, 2216)        # 任务页「开宝箱得金币」
XY_SIGNIN = (928, 1489)       # 任务页「新人签到 去查看」
XY_SHOP_TEXT = (300, 2112)    # 任务页「逛逛顶赚钱」标题(由 find_task_row 动态定位)
XY_DLG_MAIN = (540, 1531)     # 通用弹窗主按钮(开心收下/明天再来)
XY_AD_EXIT = (539, 1457)      # 广告页「坚持退出」
XY_AD_KEEP = (539, 1315)      # 广告页「领取奖励」(再看一个)
XY_AD_X = (982, 130)          # 广告页右上角 ×
XY_REWARD_OK = (540, 1411)    # 任务页「评价并收下金币」

MILE_Y = 895
MILE_X = [130, 280, 435, 587, 745, 897]
MILE_BOXES = [(120, 345), (380, 515), (540, 670), (700, 825), (860, 975)]
MILE_BG_Y = (795, 845)       # 金额数字上方的底色带


def find_task_row(a, marker="shop"):
    """动态定位任务页里的「看广告赚金币」/「逛逛顶赚钱」行。

    任务页可滚动, 位置会变, 不能写死 y。用左侧圆形图标的颜色区分:
      - 看广告: AD 徽标, 淡紫底 (228,223,249) + 深紫字 (121,95,227)
      - 逛街:   店铺图标, 亮紫 (170,85,237)
    返回该行标题文字的可点中心 (x, y); 找不到返回 None。
    """
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    if marker == "ad":
        mask = (np.abs(r - 121) < 35) & (np.abs(g - 95) < 35) & (np.abs(b - 227) < 35)
    else:
        mask = (np.abs(r - 170) < 40) & (np.abs(g - 85) < 40) & (np.abs(b - 237) < 30)
    ys, xs = np.where(mask[:, 0:200])
    if len(ys) < 60:
        return None
    yc = int(np.median(ys))
    return 300, yc


WATCH_SEC = 8 * 60            # 每轮看视频秒数
AD_ROUNDS = 3                 # 每轮做几次广告
SHOP_ROUNDS = 15              # 每轮做几次逛街(每日上限 15 次)
VIDEO_ROUNDS = 6              # 档位仍 >=1000 时, 最多连刷几轮视频
IDLE_LIMIT = 2                # 连续几轮无可做任务就退出抖音

_f = None


def shop_cooldown(a):
    """逛街是否在冷却中。

    冷却时描述行变成「NN:NN后浏览还可得金币」, 按钮是淡黄「点击领NN」;
    可用时描述是「浏览低价商品90秒即得NNN金币」。
    判据: 描述行区域出现冒号形式的倒计时(用橙色数字块判断)。
    """
    # 描述行在逛街标题下方, 相对位置: 找到店铺图标后取 +60~+110
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mask = (np.abs(r - 170) < 40) & (np.abs(g - 85) < 40) & (np.abs(b - 237) < 30)
    ys, xs = np.where(mask[:, 0:200])
    if len(ys) < 60:
        return None
    yc = int(np.median(ys))
    reg = a[yc + 55:yc + 115, 170:800]
    rr, gg, bb = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
    # 冷却倒计时 "07:26" 是橙色/红色数字
    orange = ((rr > 220) & (gg > 100) & (gg < 190) & (bb < 120)).mean()
    return orange > 0.02


def find_pending(a):
    """找出「待领取」格的中心 x。

    实测三种状态(1080x2400):
      待领取: 金额数字 **鲜红** (254,48,87) + 数字区**白底**
      已领取: 金额数字 **淡粉** (255,199,210)
      赚钱中/N分钟: 金额数字 **白** + 数字区**亮红底**
    """
    out = []
    for (x0, x1) in MILE_BOXES:
        num = a[820:895, x0 + 15:x1 - 15]
        if num.size == 0:
            continue
        r, g, b = num[:, :, 0], num[:, :, 1], num[:, :, 2]
        vivid = ((r > 225) & (g < 95) & (b < 125)).mean()

        bg = a[MILE_BG_Y[0]:MILE_BG_Y[1], x0 + 10:x1 - 10]
        rb, gb, bb = bg[:, :, 0], bg[:, :, 1], bg[:, :, 2]
        bright_red_bg = ((rb > 230) & (gb < 110) & (bb < 150)).mean()

        if vivid > 0.05 and bright_red_bg < 0.15:
            out.append((x0 + x1) // 2)
    return out


def log(m):
    global _f
    line = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), m)
    print(line, flush=True)
    if _f is None:
        _f = io.open(LOG, "a", encoding="utf-8")
    _f.write(line + "\n")
    _f.flush()

def adb(*a, timeout=45):
    cmd = [ADB] + (["-s", SERIAL] if SERIAL else []) + list(a)
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:
        return subprocess.CompletedProcess(cmd, 1, b"", b"")


def online():
    out = adb("devices", timeout=15).stdout.decode("utf-8", "ignore")
    return bool(re.search(r"\tdevice\b", out))


def adb_text(*a, timeout=45):
    return adb(*a, timeout=timeout).stdout.decode("utf-8", "ignore")


def ensure_device():
    """无线调试端口会漂移: 掉线时用 mdns 重新发现并连接。"""
    global SERIAL
    if online():
        if not SERIAL:
            SERIAL = dy_env.pick_serial()
        return True
    log("   [设备] 掉线, 尝试 mdns 重连")
    out = adb("mdns", "services", timeout=15).stdout.decode("utf-8", "ignore")
    addrs = []
    for line in out.splitlines():
        m = re.search(r"(\d+\.\d+\.\d+\.\d+):(\d+)", line)
        if m and "_adb" in line:
            a = m.group(1) + ":" + m.group(2)
            if a not in addrs:
                addrs.append(a)
    for a in addrs:
        adb("connect", a, timeout=15)
        time.sleep(1.5)
        dy_env.SERIAL = a
        SERIAL = a
        if online():
            log("   [设备] 已连 %s" % a)
            return True
    # mdns 不可用(很多 Windows 环境)时, 回退到 USB
    dy_env.SERIAL = None
    SERIAL = dy_env.pick_serial()
    if SERIAL and online():
        log("   [设备] mdns 失败, 回退 USB: %s" % SERIAL)
        return True
    return False


def check_screen():
    """分辨率不符时给出明确报错, 而不是乱点。"""
    size = dy_env.screen_size(SERIAL)
    if size is None:
        return True     # 读不到就不拦
    if size == SCREEN:
        return True
    log("!!! 屏幕分辨率 %dx%d, 但本脚本坐标按 %dx%d 标定"
        % (size[0], size[1], SCREEN[0], SCREEN[1]))
    log("!!! 所有坐标将错位。请重新标定, 或设置 FORCE_RES=1 强行运行。")
    return bool(os.environ.get("FORCE_RES"))


def tap(x, y):
    adb("shell", "input", "tap", str(x), str(y))


def swipe(a1, b1, a2, b2, ms=300):
    adb("shell", "input", "swipe", str(a1), str(b1), str(a2), str(b2), str(ms))


def back():
    adb("shell", "input", "keyevent", "KEYCODE_BACK")


def focus():
    out = adb_text("shell", "dumpsys", "window")
    last = None
    for line in out.splitlines():
        if "mCurrentFocus" in line and "=" in line:
            v = line.split("=", 1)[1].strip()
            if v and v != "null":
                last = v
    return last or "?"


def is_ad(fx=None):
    return "excitingvideo" in (fx if fx is not None else focus()).lower()


def shot():
    adb("shell", "screencap", "-p", "/sdcard/_c.png")
    adb("pull", "/sdcard/_c.png", RAW)
    adb("shell", "rm", "-f", "/sdcard/_c.png")
    try:
        return np.asarray(Image.open(RAW).convert("RGB")).astype(int)
    except Exception:
        return None


# ---------------------------------------------------------------- 画面判定
def is_feed(a):
    r, g, b = a[2310, 540]
    return r > 225 and g < 120 and b < 130


def is_task(a):
    r, g, b = a[350, 540]
    r2, g2, b2 = a[2310, 540]
    return (r > 235 and 50 < g < 160 and 90 < b < 185
            and r2 > 225 and g2 > 225 and b2 > 225)


def is_profile(a):
    """作者主页: 顶部大片白 + 九宫格封面, 或新版顶部投稿大图 + 中部红色「+关注」按钮。

    新版主页顶部是封面图(不白), 老判据会漏判并被 is_dim 当成弹窗;
    实测该布局中部「+关注」红色区域占比 0.48, 普通视频为 0.00。
    """
    if is_feed(a):
        return False
    top = a[300:420, 0:1080]
    if top.mean() > 200 and a[600, 540].mean() < 120:
        return True
    band = a[840:970, 55:860]
    r, g, b = band[:, :, 0], band[:, :, 1], band[:, :, 2]
    red = float(((r > 195) & (g < 100) & (b < 120)).mean())
    return red > 0.22


def is_dim(a):
    r, g, b = a[2310, 540]
    return r < 135 and g < 135 and b < 135


def is_shop(a, fx=None):
    """「逛街得金币」H5 页判定。

    以 BulletContainerActivity 为准(H5 页滚动后顶部标题会移出屏幕, 不能只靠像素)。
    无 fx 时才退回像素判据。
    """
    if fx is not None:
        low = fx.lower()
        if "bulletcontainer" in low:
            return True
    reg = a[110:270, 100:980]
    r, g, b = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
    pink = ((r > 225) & (g > 60) & (g < 150) & (b > 85) & (b < 175)).mean()
    return pink > 0.45


def _shop_bar(a):
    """右下角红包浮标的黄色倒计时条 (y0, y1, x0, x1); 找不到返回 None。

    浮标会浮动(实测条 y1921-1963 / y1860-1890 都出现过), 不能在固定框里判。
    黄条底色约 (255,200,101), 条内文字是玫红 (217,37,91)。
    """
    reg = a[1830:2080, 820:1060]
    r, g, b = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
    m = (r > 245) & (g > 180) & (g < 238) & (b > 85) & (b < 145)
    ys, xs = np.where(m)
    if len(ys) < 800:
        return None
    return (1830 + int(np.percentile(ys, 2)), 1830 + int(np.percentile(ys, 98)),
            820 + int(np.percentile(xs, 2)), 820 + int(np.percentile(xs, 98)))


def shop_countdown(a):
    """逛街页右下角红包倒计时条还在吗(浏览计时 或 冷却倒计时)。

    实测(1080x2400): 黄条(倒计时胶囊)颜色约 (255,200,101), 红包本体在条上方。
    """
    return _shop_bar(a) is not None


_cd_stat = (0, 0)


def shop_cd_large(a):
    """倒计时是不是 mm:ss(冷却态)。

    正常浏览浮标 = 金币图标 + "84秒"(实测 4 簇 / 跨度 69px);
    冷却浮标 = "06:39"(5 簇 / 跨度 52px)。
    所以簇数、跨度都不能用（"100秒" 也会是 5 簇）。
    唯一稳定特征是冒号: 一个很窄(<=6px)且明显偏矮的字符簇。
    条内文字是玫红 (217,37,91), 不是深色。
    """
    global _cd_stat
    bar = _shop_bar(a)
    if bar is None:
        _cd_stat = (0, 0)
        return False
    y0, y1, x0, x1 = bar
    reg = a[y0:y1 + 1, x0:x1 + 1]
    r, g, b = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
    red = (r > 180) & (r < 245) & (g < 95) & (b > 50) & (b < 140)
    if red.sum() < 30:
        _cd_stat = (0, 0)
        return False
    cols = list(red.any(0))
    runs = []
    s = None
    for x, v in enumerate(cols):
        if v and s is None:
            s = x
        elif not v and s is not None:
            runs.append((s, x - 1))
            s = None
    if s is not None:
        runs.append((s, len(cols) - 1))
    dims = []
    for (x_start, x_end) in runs:
        ys = np.where(red[:, x_start:x_end + 1].any(1))[0]
        dims.append((x_end - x_start + 1, int(ys.max() - ys.min() + 1)))
    span = (runs[-1][1] - runs[0][0]) if runs else 0
    _cd_stat = (len(runs), span)
    if not dims:
        return False
    # 金币图标的边缘会产生 w=2/h=36 的细高条, 会污染 hmax, 只用宽簇算字高
    wide = [(w, h) for (w, h) in dims if w >= 8]
    hmax = max(h for (_w, h) in wide) if wide else 0
    wmax = max(w for (w, _h) in dims)
    if not hmax:
        return False
    for (w, h) in dims:
        # 冒号: 很窄 且 高度不到字高 3/4
        if w <= 7 and h <= hmax * 0.75 and w < wmax * 0.5:
            return True
    return False


def handle_shop(timeout=200):
    """逛街得金币: 上下滑动浏览, 等右下角倒计时结束。

    注意: 进入时会弹「再看N秒可获得奖励」——点一次「继续观看」即可,
    之后**不要**再反复点它(点了会重新弹, 比正常滑动更耗时)。
    冷却态浮标显示 mm:ss, 立刻退出(返回 "cooldown")。
    """
    log("   [逛街] 上下滑动浏览")
    t0 = time.time()
    idle = 0
    leave = 0
    popup_done = False
    time.sleep(2.0)          # 等 H5 首屏加载, 否则会误判「已离开」
    while time.time() - t0 < timeout:
        if os.path.exists(STOP):
            return "stop"
        fx = focus()
        a = shot()
        if a is None:
            time.sleep(1.5)
            continue
        if "bullet" not in fx.lower() and not is_shop(a, fx):
            leave += 1
            if leave >= 3:
                log("   [逛街] 已离开 (%.0fs)" % (time.time() - t0))
                return "left"
            time.sleep(1.5)
            continue
        leave = 0
        # 冷却期: 浮标是 mm:ss, 浏览也不会涨, 直接退出
        if shop_cd_large(a):
            try:
                Image.fromarray(a.astype("uint8")).save(DEBUG_SHOP)
            except Exception:
                pass
            log("   [逛街] 冷却中(%d簇/%dpx) → 退出 (%.0fs)" % (_cd_stat[0], _cd_stat[1], time.time() - t0))
            back()
            time.sleep(2.5)
            return "cooldown"
        # 弹窗只处理一次
        if not popup_done and is_dialog_open(a):
            pos = find_dialog_button(a)
            if pos:
                log("   [逛街] 首次弹窗 → 点 %s" % (pos,))
                tap(*pos)
                time.sleep(2.5)
                popup_done = True
                continue
        if not shop_countdown(a):
            idle += 1
            if idle >= 3 and (time.time() - t0) > 15:
                log("   [逛街] 黄条消失(倒计时结束) (%.0fs)" % (time.time() - t0))
                return "left"
        else:
            idle = 0
        # 上下滑动浏览, 让浏览时长累计。间隔放宽: 太频繁会打断计时
        swipe(540, 1750, 540, 1000, random.randint(300, 450))
        time.sleep(random.uniform(4.5, 7.0))
        if random.random() < 0.35:
            swipe(540, 1050, 540, 1700, random.randint(300, 450))
            time.sleep(random.uniform(3.0, 5.0))
    log("   [逛街] 超时, BACK")
    back()
    time.sleep(1.5)
    return "timeout"


def is_live(a):
    reg = a[1940:2115, 10:240]
    r, g, b = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
    return int(((r > 200) & (g < 90) & (b > 90) & (b < 190)).sum()) > 800


def tier_width(a):
    """底部提示条 '再看X可领NNNN金币' 里红色数字块的像素宽度。

    4 位数(>=1000) 实测 74~80px; 3 位数(<1000) 明显更窄(~55px)。
    """
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    red = (r > 195) & (g < 115) & (b < 135)
    prof = red[1070:1125, :620].sum(0)
    runs = []
    s = None
    for x in range(620):
        if prof[x] > 0 and s is None:
            s = x
        elif prof[x] == 0 and s is not None:
            runs.append((s, x))
            s = None
    if s is not None:
        runs.append((s, 620))
    out = []
    for rg in runs:
        if out and rg[0] - out[-1][1] < 12:
            out[-1] = [out[-1][0], rg[1]]
        else:
            out.append([rg[0], rg[1]])
    return (out[0][1] - out[0][0]) if out else 0


TIER_SMALL_W = 62   # 低于该宽度视为档位 < 1000 金币


def tier_small(a):
    w = tier_width(a)
    return 0 < w < TIER_SMALL_W


def ad_exit_dialog(a):
    """广告页「再看一个视频额外获得」弹窗: 白卡 + 中间亮粉药丸。

    弹窗位置会浮动(实测 y1238-1374 / y1100-1377 都出现过), 不能写死 y。
    判据: 中部有大片白卡, 且卡内有亮粉红宽药丸。
    """
    card = a[600:1450, 100:980]
    if card.size == 0:
        return False
    white_ratio = (card.min(2) > 235).mean()
    if white_ratio < 0.35:
        return False
    reg = a[1000:1450, 200:880]
    r, g, b = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
    pink = ((r > 235) & (g > 30) & (g < 115) & (b > 85) & (b < 165)).mean()
    return pink > 0.05


def _ad_pink_pill(a):
    """广告弹窗里亮粉药丸的 (y0, y1); 找不到返回 None。"""
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    pink = (r > 235) & (g > 30) & (g < 115) & (b > 85) & (b < 165)
    prof = pink[:, 200:880].sum(1)
    runs = []
    st = None
    for y in range(2400):
        if prof[y] > 200 and st is None:
            st = y
        elif prof[y] <= 200 and st is not None:
            runs.append([st, y])
            st = None
    merged = []
    for rg in runs:
        if merged and rg[0] - merged[-1][1] < 20:
            merged[-1][1] = rg[1]
        else:
            merged.append(rg)
    merged = [rg for rg in merged if rg[1] - rg[0] > 80]
    return tuple(merged[-1]) if merged else None


def ad_waiting(a):
    """广告弹窗是「再看N秒可领奖励」等待态吗?

    等待态: 药丸 = 继续观看, 下方并排两段文字「坚持退出 | 换一个」
    完成态: 药丸 = 领取奖励, 下方只有一段「坚持退出」
    判据: 药丸下方第一行文字的**字符块数量**(按列间隙分组)。
    """
    pill = _ad_pink_pill(a)
    if pill is None:
        return False
    yb = pill[1]
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    # 只取深色文字(灰字 ~130, 橙字 ~230,80,60), 排除浅背景
    text = ((r < 190) & (g < 160) & (b < 160)) | ((r > 190) & (g < 140) & (b < 120))
    band = text[yb + 25:yb + 130, 220:860]
    colsum = band.sum(0)
    runs = []
    st = None
    for x in range(len(colsum)):
        if colsum[x] > 3 and st is None:
            st = x
        elif colsum[x] <= 3 and st is not None:
            if x - st >= 8:
                runs.append((st, x))
            st = None
    # 按间隙 > 30px 合并成"段"
    groups = []
    for rg in runs:
        if groups and rg[0] - groups[-1][1] < 30:
            groups[-1] = [groups[-1][0], rg[1]]
        else:
            groups.append([rg[0], rg[1]])
    return len(groups) >= 2


def find_ad_continue(a):
    """等待态的「继续观看」= 亮粉药丸本身。"""
    pill = _ad_pink_pill(a)
    if pill is None:
        return None
    return 540, (pill[0] + pill[1]) // 2


def find_ad_exit(a):
    """「坚持退出」: 亮粉药丸下方、白卡内部的灰字。"""
    pill = _ad_pink_pill(a)
    if pill is None:
        return None
    yb = pill[1]
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    white = (r > 240) & (g > 240) & (b > 240)
    col = white[:, 300:780].sum(1)
    card_bottom = yb
    for y in range(yb, min(yb + 400, 2390)):
        if col[y] > 200:
            card_bottom = y
        elif y > card_bottom + 30:
            break
    gray = (np.abs(r - g) < 22) & (np.abs(g - b) < 22) & (r > 110) & (r < 200)
    band = gray[yb + 20:card_bottom, 250:830]
    prof = band.sum(1)
    rows = [i for i, v in enumerate(prof) if v > 8]
    if rows:
        return 540, yb + 20 + (rows[0] + rows[-1]) // 2
    return 540, yb + 90



# ---------------------------------------------------------------- 通用恢复
def is_dialog_open(a):
    """判断是否有真正的模态弹窗(不是页面遮罩)。

    真弹窗特征: 屏幕四角明显变暗 + 中间有大片白色圆角卡片。
    注意: feed 页底部有亮红「赚钱」按钮, 必须优先排除, 否则视频切换瞬间的暗帧
    会被误判成弹窗, 导致在 feed 里狂按 BACK 退出 App。
    """
    if is_feed(a):
        return False
    corners = [a[100:200, 40:140], a[100:200, 940:1040],
               a[2200:2300, 40:140], a[2200:2300, 940:1040]]
    dim = sum(1 for c in corners if c.mean() < 120)
    if dim < 3:
        return False
    center = a[900:1500, 200:880]
    return center.mean() > 200


def find_dialog_button(a):
    """在**已确认的弹窗**里按颜色找主按钮, 返回 (x, y)。

    实测三种弹窗按钮:
      领取弹窗(开心收下/红)   y1408-1557
      开宝箱弹窗(开心收下/红) y1280-1430
      签到弹窗(明天再来/淡黄) y1481-1609
    关键: 必须限定在**白色卡片内部**, 否则会点到卡片外的页面按钮(如「立即下载」)。
    """
    if not is_dialog_open(a):
        return None
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]

    # 先找白色卡片的横向范围(卡片是圆角白底)
    white = (r > 238) & (g > 238) & (b > 238)
    col = white.sum(0)
    xs = [x for x in range(60, 1020) if col[x] > 300]
    if xs:
        cx0, cx1 = xs[0], xs[-1]
    else:
        cx0, cx1 = 150, 930

    red = (r > 235) & (g < 110) & (b > 60) & (b < 160)
    pale = (r > 245) & (g > 215) & (g < 250) & (b > 150) & (b < 215)
    # 红按钮=主按钮(继续观看/开心收下), 浅色=次按钮(坚持退出/明天再来)。
    # 逛街「再看N秒可获得奖励」弹窗里次按钮就是「坚持退出」, 优先级必须红在前,
    # 否则会主动把正在浏览的奖励点掉。
    for mask in (red, pale):
        # 只在卡片内部统计
        prof = mask[:, cx0 + 20:cx1 - 20].sum(1)
        runs = []
        s = None
        for y in range(2400):
            if prof[y] > 250 and s is None:
                s = y
            elif prof[y] <= 250 and s is not None:
                runs.append((s, y))
                s = None
        runs = [rg for rg in runs if rg[1] - rg[0] > 60]
        if runs:
            y0, y1 = runs[-1]
            return 540, (y0 + y1) // 2
    return None


def handle_dialog():
    """仅当确认有遮罩弹窗时才找按钮点, 否则 BACK。"""
    a = shot()
    if a is None or not is_dim(a):
        back()
        time.sleep(1.0)
        return
    pos = find_dialog_button(a)
    if pos is None:
        if PKG not in focus():
            # 已经退到桌面/别的 App: BACK 只会在桌面白按, 直接重拉抖音
            log("   [弹窗] 不在抖音 → 重启")
            relaunch()
            return
        log("   [弹窗] 找不到按钮 → BACK")
        back()
        time.sleep(1.2)
        return
    log("   [弹窗] 点主按钮 %s" % (pos,))
    tap(*pos)
    time.sleep(1.8)
    a = shot()
    if a is not None and is_dim(a):
        back()
        time.sleep(1.2)


def ad_reward_claimed(a):
    """广告页右上角是否已显示「领取成功」。

    实测: 倒计时「29秒后可领奖励 ×」white=0.072 / 12 簇;
          完成「领取成功 ×」white=0.047 / 7 簇 —— 字少一半, 用占比+簇数区分。
    """
    reg = a[95:165, 730:1035]
    if reg.size == 0:
        return False
    r, g, b = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
    w = (r > 215) & (g > 215) & (b > 215)
    ratio = float(w.mean())
    if ratio < 0.02:
        return False
    runs, st = 0, False
    for v in w.any(0):
        if v and not st:
            runs += 1
        st = bool(v)
    return ratio < 0.058 and runs <= 8


def _pill_stat(a):
    """右上角药丸的白字统计 (白字占比, 字符簇数), 用于区分倒计时/领取成功。"""
    reg = a[95:165, 690:1035]
    r, g, b = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
    w = (r > 215) & (g > 215) & (b > 215)
    runs, st = 0, False
    for v in w.any(0):
        if v and not st:
            runs += 1
        st = bool(v)
    return float(w.mean()), runs


def handle_ad_page(timeout=150):
    """停在广告页(ExcitingVideoActivity)时, 等它放完并退出。"""
    log("   [广告页] 开始处理")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(STOP):
            return
        fx = focus()
        if not is_ad(fx):
            log("   [广告页] 已离开 (%.0fs)" % (time.time() - t0))
            return
        a = shot()
        if a is None:
            time.sleep(1.0)
            continue
        if ad_exit_dialog(a):
            # 先区分: 等待态(还差N秒) → 点继续观看; 完成态 → 点坚持退出
            if ad_waiting(a):
                pos = find_ad_continue(a)
                if pos:
                    log("   [广告页] 还差N秒, 点 继续观看 %s" % (pos,))
                    tap(*pos)
                    time.sleep(2.0)
                    continue
            pos = find_ad_exit(a)
            if pos is None:
                pos = XY_AD_EXIT
            log("   [广告页] 奖励已到 → 点 坚持退出 %s" % (pos,))
            tap(*pos)
            time.sleep(2.5)
            continue
        # 没有弹窗 ≠ 已经领完: 广告播放中右上角药丸同样有白字(「N秒后可领奖励」),
        # 早退会把播放器打断, 退回去又落进「再看N秒可获得奖励」弹窗, 形成死循环。
        # 只有长时间完全没有弹窗, 才当"停在落地页"处理。
        if _ad_dbg[0] % 5 == 0:
            _ad_dbg[0] += 1
            log("   [广告页] 药丸 white=%.3f 簇=%d" % _pill_stat(a))
            try:
                from PIL import Image as _I2
                _I2.fromarray(a[60:220, 650:1060].astype("uint8")).save(TMP + r"\pill_%d.png" % (_ad_dbg[0] // 5))
            except Exception:
                pass
        _ad_dbg[0] += 1
        # 右上角已变成「领取成功」→ 这条广告放完了, 立刻退出去领奖
        if ad_reward_claimed(a) and (time.time() - t0) > 6:
            log("   [广告页] 右上角显示领取成功 → 退出领奖")
            for _ in range(2):
                back()
                time.sleep(1.5)
                if not is_ad():
                    return
            time.sleep(1.0)
            continue
        if time.time() - t0 > 100:
            log("   [广告页] 100s 无弹窗, 判定停在落地页 → BACK")
            back()
            time.sleep(1.5)
            return
        time.sleep(1.5)
    log("   [广告页] 超时, BACK 退出")
    back()
    time.sleep(1.5)


def relaunch():
    """重启抖音到首页。"""
    log("   [恢复] 重启抖音")
    adb("shell", "am", "force-stop", PKG)
    time.sleep(2)
    adb("shell", "monkey", "-p", PKG, "-c",
        "android.intent.category.LAUNCHER", "1")
    time.sleep(4)
    # 冷启动常超过 10s: 轮询等 feed 的「赚钱」红按钮出现, 最多再等 ~30s
    for _ in range(20):
        if os.path.exists(STOP):
            return
        a = shot()
        if a is not None and is_feed(a):
            time.sleep(1.5)
            return
        time.sleep(1.5)


def escape_to_task(tries=10):
    """从任意页面挣扎回「赚钱任务」页。"""
    for i in range(tries):
        if os.path.exists(STOP):
            return False
        fx = focus()
        a = shot()
        if a is None:
            time.sleep(1.0)
            continue
        if is_ad(fx):
            handle_ad_page()
            continue
        if is_shop(a, fx) or "bullet" in fx.lower():
            # 逛街得金币等 H5 页: 能领就领完再退
            if shop_countdown(a):
                handle_shop()
            back()
            time.sleep(2.0)
            continue
        if is_task(a):
            if is_dim(a):
                handle_dialog()
                continue
            maybe_claim()
            return True
        if is_dim(a):
            handle_dialog()
            continue
        if is_profile(a):
            log("   [恢复] 在UP主主页 → BACK")
            back()
            time.sleep(1.8)
            continue
        if is_feed(a):
            tap(*XY_MAKE_MONEY)
            time.sleep(2.5)
            continue
        back()
        time.sleep(1.8)
    if i >= tries - 1:
        relaunch()
    return False


def go_feed(tries=6):
    for _ in range(tries):
        fx = focus()
        if PKG not in fx:
            # 弹窗误判连按 BACK 会把 App 退到桌面, 这里必须重新拉起
            log("   [恢复] 不在抖音 → 重启")
            relaunch()
            time.sleep(4)
            continue
        a = shot()
        if a is None:
            time.sleep(1.0); continue
        if is_ad(fx):
            handle_ad_page(); continue
        if is_dim(a):
            handle_dialog(); continue
        if is_feed(a):
            return True
        if is_profile(a):
            log("   [恢复] 在UP主主页 → BACK")
            back(); time.sleep(1.8); continue
        if is_task(a):
            tap(*XY_WATCH_VIDEO); time.sleep(3.5); continue
        back(); time.sleep(1.8)
    return False


# ---------------------------------------------------------------- 任务动作
def _task_scroll_top():
    """任务页滚回顶部, 保证「新人看视频赚金币」的档位条在屏幕内。"""
    for _ in range(2):
        swipe(540, 950, 540, 1900, random.randint(240, 320))
        time.sleep(0.45)
    time.sleep(0.3)


def claim_milestones():
    """点掉所有「待领取」档位。返回是否确实点下去了。"""
    a = shot()
    if a is None or not is_task(a):
        return False
    if is_dim(a):
        handle_dialog()
        return False
    xs = find_pending(a)
    if not xs:
        # 档位条可能被滚到屏幕外(逛街/广告行), 回顶部再判一次
        _task_scroll_top()
        a = shot()
        if a is None or not is_task(a):
            return False
        xs = find_pending(a)
        if not xs:
            detail = []
            for (x0, x1) in MILE_BOXES:
                num = a[820:895, x0 + 15:x1 - 15]
                r, g, b = num[:, :, 0], num[:, :, 1], num[:, :, 2]
                detail.append("%.2f" % float(((r > 225) & (g < 95) & (b < 125)).mean()))
            log("   [领奖] 无待领取 vivid=%s tier=%d" % (",".join(detail), tier_width(a)))
            if _dbg[0] < 5:
                _dbg[0] += 1
                try:
                    from PIL import Image as _Im
                    _Im.fromarray(a.astype("uint8")).save(DEBUG_SHOT)
                except Exception:
                    pass
            return False
    log("   [领奖] 待领取 x=%s" % (xs,))
    for x in xs:
        tap(x, MILE_Y)
        time.sleep(1.2)
        b = shot()
        if b is not None and not is_task(b):
            # 领奖弹窗有白卡片, is_dim 不一定命中, 统一交给 handle_dialog
            handle_dialog()
        time.sleep(0.8)
    return True


claim_milestones_claim = claim_milestones

_last_claim = 0.0
_dbg = [0]


def maybe_claim(min_gap=25.0):
    """任何时刻回到任务页都顺手领一次, 避免档位解锁后长时间没人领。"""
    global _last_claim
    if time.time() - _last_claim < min_gap:
        return False
    _last_claim = time.time()
    return claim_milestones()


def do_chest():
    """开宝箱。返回是否真的领到了金币。"""
    a = shot()
    if a is None or not is_task(a):
        return False
    log("   [开宝箱]")
    tap(*XY_CHEST)
    time.sleep(3.0)
    if is_ad():
        handle_ad_page()
        return True
    b = shot()
    if b is not None and is_dim(b):
        if find_dialog_button(b) is not None:
            handle_dialog()
            return True
        handle_dialog()
        return False
    return False


def do_signin():
    """签到。返回是否真的执行了签到动作。"""
    a = shot()
    if a is None or not is_task(a):
        return False
    log("   [签到]")
    tap(*XY_SIGNIN)
    time.sleep(3.0)
    b = shot()
    if b is not None and is_dim(b):
        if find_dialog_button(b) is not None:
            handle_dialog()
            return True
        handle_dialog()
        return False
    return False


def do_ad():
    """从任务页点「看广告赚金币」标题看一次广告。"""
    a = shot()
    pos = find_task_row(a, "ad") if a is not None else None
    if pos is None:
        log("   [看广告] 找不到广告行")
        return False
    log("   [看广告] 点 %s" % (pos,))
    tap(*pos)
    time.sleep(4.0)
    if not is_ad():
        a = shot()
        if a is not None and is_dim(a):
            handle_dialog()
            return False
        log("   [看广告] 没进广告页(可能冷却中)")
        return False
    handle_ad_page()
    escape_to_task()
    return True


def do_shop():
    """从任务页点「逛逛顶赚钱」进 H5 页, 滑动浏览 90 秒领金币。每日 15 次。

    返回 'done' 表示完成一次, 'cooldown' 表示在冷却(应转去看广告), 'fail' 其他。
    """
    # 必须先回到任务页, 否则会把 H5 页当成任务页去找逛街行
    if not escape_to_task():
        log("   [逛街] 回不到任务页")
        return "fail"
    a = shot()
    if a is None or not is_task(a):
        return "fail"
    pos = find_task_row(a, "shop")
    if pos is None:
        log("   [逛街] 找不到逛街行")
        return "fail"
    log("   [逛街] 点 %s" % (pos,))
    tap(*pos)
    time.sleep(2.0)
    # H5 首屏可能很慢, 轮询最多 ~14 秒确认是否真的进了逛街页
    entered = False
    a = None
    fx = ""
    for _ in range(6):
        time.sleep(2.0)
        fx = focus()
        a = shot()
        if a is None:
            continue
        if is_ad(fx):
            handle_ad_page()
            return "fail"
        if is_shop(a, fx):
            entered = True
            break
    if not entered:
        if a is not None and is_dim(a):
            handle_dialog()
        log("   [逛街] 没进逛街页 → 冷却中")
        return "cooldown"
    if is_dim(a):
        handle_dialog()
    # 进页面后先看倒计时: 数字特别大 = 冷却中, 立刻退出转下一个任务
    t0 = time.time()
    while time.time() - t0 < 16:
        b = shot()
        if b is None:
            break
        fx2 = focus()
        if not is_shop(b, fx2):
            break
        if shop_cd_large(b):
            log("   [逛街] 倒计时是 mm:ss(%d簇/%dpx) → 冷却中, 退出转下一任务" % _cd_stat)
            back()
            time.sleep(2.5)
            return "cooldown"
        if shop_countdown(b):
            break
        time.sleep(2.0)
    r = handle_shop()
    escape_to_task()
    return "cooldown" if r == "cooldown" else "done"


def watch_feed(sec):
    """复用 dy_auto 的拟人化看视频逻辑(折线滑动/重尾停留/环驱动/直播间跳过)。

    on_tick 钩子负责处理激励视频广告和弹窗, 避免 dy_auto 把广告页当作普通视频。
    """
    log("   [看视频] 复用 dy_auto 拟人化观看 %.0f 秒" % sec)
    sys.path.insert(0, TMP)
    import dy_auto
    dy_auto.TMP = TMP
    dy_auto.LOG = LOG

    def hook():
        if os.path.exists(STOP):
            return False
        fx = focus()
        a = shot()
        if a is None:
            return False
        if is_ad(fx):
            log("   [看视频] 遇到广告页 → 处理")
            handle_ad_page()
            return True
        if is_profile(a):
            log("   [看视频] 误入UP主主页 → BACK 退出")
            t0 = time.time()
            back_to_feed = False
            for _ in range(3):
                back()
                time.sleep(0.35)
                b = shot()
                if b is None:
                    continue
                if not is_profile(b):
                    back_to_feed = is_feed(b)
                    break
            if back_to_feed:
                # 刚才是从这条视频点进主页的, 退出后直接划走, 不再停留
                dy_auto.quick_swipe(2)
                log("   [看视频] 主页退出后上滑两下 (%.1fs)" % (time.time() - t0))
            else:
                log("   [看视频] 主页已退出 (%.1fs)" % (time.time() - t0))
            return True
        if is_dim(a):
            handle_dialog()
            return True
        if not is_feed(a):
            log("   [看视频] 不在feed → 尝试回到feed")
            go_feed()
            return True
        return False

    try:
        dy_auto.main(minutes=sec / 60.0, on_tick=hook, fast=True)
    except Exception as e:
        log("   [看视频] dy_auto 异常: %r" % (e,))
    log("   [看视频] 本轮结束")


# ---------------------------------------------------------------- 主循环
def read_tier():
    """读当前档位金额是否 <1000 (True/False/None=读不到)。

    实测 7 日档位全部领完后, 卡片里只剩「继续看视频赚超多金币 666」,
    tier_width() 会把这个 3 位数当成"档位已降到 <1000" → 直接放弃看视频。
    而看视频本身每天都有金币收益, 放弃是错的。
    所以这里只认「明确读到大额档位」的情形, 其余一律继续看视频。
    """
    a = shot()
    if a is None:
        return None
    if not is_task(a):
        return None
    # 只在大额档位(>=1000)时返回 False; 读不准/读不到都按 False 处理
    return False


def quit_douyin(reason=""):
    """没有可做的任务 → 退出抖音(回桌面), 然后结束脚本。"""
    log("==== 无可做任务%s → 退出抖音 ====" % ((" (" + reason + ")") if reason else ""))
    adb("shell", "am", "force-stop", PKG)
    time.sleep(2)
    # 回桌面
    adb("shell", "input", "keyevent", "KEYCODE_HOME")
    time.sleep(1.5)
    log("==== 已退出抖音 ====")


LOCK = TMP + r"\coin.lock"


def acquire_lock():
    """单实例锁: 防止重复启动导致两个脚本互相抢设备。"""
    me = os.getpid()
    if os.path.exists(LOCK):
        try:
            other = int(io.open(LOCK, encoding="utf-8").read().strip())
        except Exception:
            other = 0
        if other and other != me and _pid_alive(other):
            log("已有实例在运行 (PID %d), 本实例退出" % other)
            return False
    io.open(LOCK, "w", encoding="utf-8").write(str(me))
    return True


def _pid_alive(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                             capture_output=True, timeout=10)
        return str(pid) in out.stdout.decode("utf-8", "ignore")
    except Exception:
        return False


def release_lock():
    try:
        if os.path.exists(LOCK):
            cur = io.open(LOCK, encoding="utf-8").read().strip()
            if cur == str(os.getpid()):
                os.remove(LOCK)
    except Exception:
        pass


def one_round(i):
    log("==== 第 %d 轮 ====" % i)
    if not escape_to_task():
        log("   无法回到任务页, 跳过")
        return False

    did_something = False

    # ---- 1) 优先看视频: 档位 >=1000 时一直刷 ----
    for k in range(VIDEO_ROUNDS):
        if os.path.exists(STOP):
            return did_something
        small = read_tier()
        if small is None:
            log("   [档位] 读不到, 当作大档位继续看视频")
        elif small:
            log("   [档位] 已降到 <1000 金币 → 停止看视频")
            break
        else:
            log("   [档位] 仍 >=1000 金币, 继续看视频 (%d/%d)" % (k + 1, VIDEO_ROUNDS))
        claim_milestones()
        if go_feed():
            watch_feed(WATCH_SEC)
            did_something = True
        else:
            log("   进不了看视频页")
        escape_to_task()
        if claim_milestones_claim():
            did_something = True

    # ---- 2) 逛街 + 广告交错: 逛街冷却就去看广告 ----
    log("==== 第 %d 轮: 逛街 / 广告 ====" % i)
    ad_left = AD_ROUNDS
    shop_done = 0
    for k in range(SHOP_ROUNDS):
        if os.path.exists(STOP):
            return did_something
        if not escape_to_task():
            break
        log("   逛街 %d/%d" % (k + 1, SHOP_ROUNDS))
        r = do_shop()
        if r == "done":
            shop_done += 1
            did_something = True
            continue
        if r == "cooldown":
            log("   → 逛街冷却, 转去看广告")
            while ad_left > 0:
                if os.path.exists(STOP):
                    return did_something
                if not escape_to_task():
                    break
                log("   广告 %d/%d" % (AD_ROUNDS - ad_left + 1, AD_ROUNDS))
                if not do_ad():
                    ad_left = 0
                    break
                ad_left -= 1
                did_something = True
                time.sleep(3)
            if not escape_to_task():
                break
            r2 = do_shop()
            if r2 == "done":
                shop_done += 1
                did_something = True
                continue
            log("   逛街与广告都不可用")
            break
        break
    log("   本轮逛街完成 %d 次, 广告剩 %d" % (shop_done, ad_left))

    # 剩余的广告补做
    for k in range(ad_left):
        if os.path.exists(STOP):
            return did_something
        log("   补做广告 %d/%d" % (k + 1, ad_left))
        escape_to_task()
        if not do_ad():
            break
        did_something = True
        time.sleep(3)

    escape_to_task()
    if do_chest():
        did_something = True
    escape_to_task()
    if do_signin():
        did_something = True
    escape_to_task()
    return did_something


def main():
    global _f
    if not acquire_lock():
        return
    _f = io.open(LOG, "a", encoding="utf-8")
    if os.path.exists(STOP):
        os.remove(STOP)
    global WATCH_SEC
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if len(sys.argv) > 2:
        WATCH_SEC = int(sys.argv[2])
    log("==== dy_coin 启动 (rounds=%s, watch=%ds) ====" % (n or "无穷", WATCH_SEC))
    log("   [环境] %s" % dy_env.describe())
    if not ensure_device():
        log("!!! 没有可用设备: 请确认 USB 调试已授权, 或 adb connect IP:PORT 成功")
        release_lock()
        return
    if not check_screen():
        log("!!! 分辨率不匹配, 已中止 (可用 FORCE_RES=1 强行运行)")
        release_lock()
        return
    i = 0
    idle_rounds = 0
    while True:
        if os.path.exists(STOP):
            log("检测到 coin.stop, 退出")
            break
        i += 1
        if n and i > n:
            break
        try:
            if not ensure_device():
                log("设备不可用, 等待重连")
                time.sleep(10)
                continue
            acted = one_round(i)
            if acted:
                idle_rounds = 0
            else:
                idle_rounds += 1
                log("本轮无可做任务 (%d/%d)" % (idle_rounds, IDLE_LIMIT))
                if idle_rounds >= IDLE_LIMIT:
                    quit_douyin("连续 %d 轮无可做任务" % idle_rounds)
                    break
                time.sleep(30)
        except Exception as e:
            log("异常已忽略: %r" % (e,))
            time.sleep(3)
    log("==== dy_coin 结束 ====")
    release_lock()


if __name__ == "__main__":
    main()
