# -*- coding: utf-8 -*-
"""抖音极速版拟人化观看 + 直播间 3 秒上滑。

风控对抗要点(全部经实机标定, 见文末"标定记录"):
  1. 进度环色域修正: 环本体色 #FFE3AE (B=174), 原判据 b<150 把环自身排除。
     实测 b<215 命中角度 80~103/120, b<150 仅 7~16/120。
  2. 弧长改为"最长连续段": 原 extent() 从 12 点起遇首个缺口即 break 返回 0。
  3. 停留时长改重尾混合分布(对数正态), 消除固定 15s+12s 兜底造成的 20~25s 机械峰值。
  4. 滑动改 3~6 点 motionevent 折线 + smoothstep 速度曲线 + 横向漂移,
     替代 input swipe 的纯直线; 按设备端 73ms/次命令开销反算 sleep 以还原真实时长。
  5. 加入犹豫半滑 / 回看 / 长休息 等真人行为, 点赞概率按时长加权。
  6. 弹窗检测降频并加抖动, 减少 uiautomator dump 造成的 accessibility_enabled 翻转。

标定记录(2026-10-17, vivo V2055A / Android 14 / 1080x2400 / SELinux Enforcing):
  - 触摸屏 /dev/input/event3 (vivo_ts), 原始坐标空间 1620x3600。
  - sendevent 写 event3 被 SELinux 拒绝; input 命令可用, 每点固定开销 73ms。
  - input swipe 参数空间: 时长 120~700ms、距离 500~1300px 均可翻页。
  - 进度环: 拟合圆心约 (138,511) R=62; 12 点起顺时针填充。
    (2026-10-18 复测: 实际圆心漂移到 ~(139,602) R~64, redfrac/RING_BOX 已同步下移约 90px;
     旧区域 y500-585 会完全漏掉红包实体 y589-682, 导致红包帧被误判为"无红包"。)
  - 直播间房间: 独立 Activity com.ss.android.ugc.aweme.live.LivePlayActivity,
     直接 BACK 退出即可回到 feed。(焦点被子窗口/PopupWindow 抢占时,
     focus() 回退 mFocusedApp 判 Activity, 避免浮层遮挡时漏判。)
  - 直播间判据: 左下角洋红标签 #F52A81 命中 6130~8460px, 普通视频恒为 0。
"""
import subprocess, time, sys, io, os, re, math, random
from PIL import Image
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import dy_env

ADB = dy_env.ADB
SERIAL = dy_env.SERIAL
TMP = os.environ.get("DYAUTO_TMP") or os.path.dirname(os.path.abspath(__file__))
if not os.path.isdir(TMP):
    os.makedirs(TMP, exist_ok=True)
SHOT = os.path.join(TMP, "_d.png")
LOG = os.path.join(TMP, "dy.log")
PKG = "com.ss.android.ugc.aweme.lite"
DEV_HOST = os.environ.get("DY_DEV_HOST", "")   # 无线调试设备的 IP(可选, 一般靠 mdns 自动发现)
DEV = os.environ.get("DY_DEV_ADDR", "")        # 可选兜底地址 IP:PORT(端口会漂移)

# 设备端单次 input 命令固定开销(实测 73ms), 用于反算轨迹 sleep
CMD_OVERHEAD_MS = 73


def adb(*a, timeout=30):
    cmd = [ADB] + (["-s", SERIAL] if SERIAL else []) + list(a)
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:
        return subprocess.CompletedProcess(cmd, 1, b"", b"")


def log(m):
    line = time.strftime("%H:%M:%S ") + m
    print(line, flush=True)
    try:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ln(mu, sigma, lo, hi):
    """对数正态采样, 用于生成重尾的真人行为时长。"""
    return max(lo, min(hi, math.exp(random.gauss(mu, sigma))))


def discover_dev():
    """用 mdns 发现无线调试设备地址(端口会随重连漂移)。返回候选 addr 列表。"""
    out = adb("mdns", "services", timeout=15).stdout.decode("utf-8", "ignore")
    cands = []
    for line in out.splitlines():
        if "_adb" in line:
            m = re.search(r"(\d+\.\d+\.\d+\.\d+):(\d+)", line)
            if m:
                cands.append(m.group(1) + ":" + m.group(2))
    return cands


def _online():
    out = adb("devices", timeout=15).stdout.decode("utf-8", "ignore")
    return bool(re.search(r"\tdevice\b", out))


def ensure_device():
    global SERIAL
    if _online():
        if not SERIAL:
            SERIAL = dy_env.pick_serial()
        return True
    # 1) 优先 mdns 现发现(端口漂移后仍能连上)
    for addr in discover_dev():
        adb("connect", addr, timeout=15)
        time.sleep(1.2)
        SERIAL = addr
        dy_env.SERIAL = addr
        if _online():
            return True
    # 2) 兜底: 固定 IP 常见端口
    cands = [x for x in (DEV,
                         (DEV_HOST + ":5555") if DEV_HOST else "",
                         (DEV_HOST + ":42201") if DEV_HOST else "") if x]
    for addr in cands:
        adb("connect", addr, timeout=15)
        time.sleep(0.8)
        SERIAL = addr
        dy_env.SERIAL = addr
        if _online():
            return True
    # 3) 回退 USB
    SERIAL = dy_env.pick_serial()
    dy_env.SERIAL = SERIAL
    return bool(SERIAL) and _online()


def focus():
    """返回当前焦点窗口所属的 Activity 行。

    子窗口/PopupWindow 抢占焦点时回退到 mFocusedApp, 以免漏判
    (直播间内弹出的礼物/领奖浮层会把 mCurrentFocus 变成 PopupWindow)。
    """
    try:
        o = adb("shell", "dumpsys", "window").stdout.decode("utf-8", "ignore")
        cur = ""
        app = ""
        for l in o.splitlines():
            if not cur and "mCurrentFocus" in l and "null" not in l:
                cur = l
            if not app and "mFocusedApp" in l:
                app = l
        if cur and "PopupWindow" not in cur:
            return cur
        return app or cur
    except Exception:
        pass
    return ""


def grab():
    for _ in range(3):
        adb("shell", "screencap", "-p", "/sdcard/_d.png")
        adb("pull", "/sdcard/_d.png", SHOT)
        adb("shell", "rm", "/sdcard/_d.png")
        try:
            return np.asarray(Image.open(SHOT).convert("RGB")).astype(int)
        except Exception:
            time.sleep(0.4)
    return None


# ---------------- 视觉判据 ----------------

# 左上红包圈: 实机复测圆心约 (139,602) R~64 (2026-10-18 校准)
CX, CY = 139, 602
RING_BOX = (60, 515, 220, 690)


def redfrac(a):
    """红包视频判据: 左上角红包实体(环内)红色占比。校准区域 y585-690 x70-175。"""
    sub = a[585:690, 70:175]
    r, g, b = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    return float(((r > 165) & (g < 100) & (b < 100) & (r - g > 70)).mean())


def is_profile(a):
    """作者主页: 顶部大片白 + 九宫格, 或新版顶部封面大图 + 中部红色「+关注」按钮。"""
    if is_feed(a):
        return False
    top = a[300:420, 0:1080]
    if top.mean() > 200 and a[600, 540].mean() < 120:
        return True
    band = a[840:970, 55:860]
    r, g, b = band[:, :, 0], band[:, :, 1], band[:, :, 2]
    red = float(((r > 195) & (g < 100) & (b < 120)).mean())
    return red > 0.22


def is_feed(a):
    """feed 底部「赚钱」红按钮亮起。与 dy_coin.is_feed 一致。"""
    r, g, b = a[2310, 540]
    return r > 225 and g < 120 and b < 130


def goldmask(a):
    """金色掩膜。b<215 是修正值: 环本体 #FFE3AE 的 B=174 会被 b<150 排除。"""
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return (r > 185) & (g > 140) & (b < 215) & ((r - b) > 60) & ((g - b) > 25)


def _fitc(xs, ys):
    A = np.stack([xs.astype(float), ys.astype(float), np.ones(len(xs))], axis=1)
    bb = -(xs.astype(float) ** 2 + ys.astype(float) ** 2)
    sol, *_ = np.linalg.lstsq(A, bb, rcond=None)
    D, E, F = sol
    cx, cy = -D / 2.0, -E / 2.0
    r2 = cx * cx + cy * cy - F
    return cx, cy, (math.sqrt(r2) if r2 > 0 else 1.0)


def ring_arc(a):
    """返回 (弧长角度, 半径)。弧长取环上最长连续段, 不假设起点在 12 点。"""
    m = goldmask(a)
    H, W = m.shape
    x0, y0, x1, y1 = RING_BOX
    sub = m[y0:y1, x0:x1]
    ys, xs = np.nonzero(sub)
    if len(xs) < 40:
        return 0, 0.0
    xs = xs + x0
    ys = ys + y0
    cx, cy, R = _fitc(xs, ys)
    for _ in range(3):
        d = np.abs(np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) - R)
        k = d < max(6.0, R * 0.12)
        if k.sum() < 30:
            break
        xs, ys = xs[k], ys[k]
        cx, cy, R = _fitc(xs, ys)
    if R < 20:
        return 0, 0.0
    hit = [False] * 120
    for i, deg in enumerate(range(0, 360, 3)):
        rad = math.radians(deg - 90)
        for rr in np.arange(R - 9, R + 9, 1.5):
            x = int(round(cx + rr * math.cos(rad)))
            y = int(round(cy + rr * math.sin(rad)))
            if 0 <= y < H and 0 <= x < W and m[y, x]:
                hit[i] = True
                break
    best = cur = 0
    for v in hit + hit:
        cur = cur + 1 if v else 0
        if cur > best:
            best = cur
    return min(best, 120) * 3, R


# 直播间预览卡片的「直播中」洋红标签 (#F52A81 系), 位于左下角
# 实测: 直播预览命中 6130~8460 px, 普通视频/直播中/其它页面恒为 0
LIVE_Y0, LIVE_Y1, LIVE_X0, LIVE_X1 = 1940, 2115, 10, 240
LIVE_HIT = 800
LIVE_DWELL = 2.0


def live_score(a):
    sub = a[LIVE_Y0:LIVE_Y1, LIVE_X0:LIVE_X1]
    r, g, b = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    m = (r > 225) & (g > 20) & (g < 90) & (b > 95) & (b < 175) & (r - g > 130)
    return int(m.sum())


def live_watch(seconds):
    """直播间预览: 停留 seconds 秒; 期间复检, 若已不是直播间则返回 False。"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(min(0.5, max(0.05, seconds - (time.time() - t0))))
        if time.time() - t0 >= seconds:
            break
        a2 = grab()
        if a2 is None:
            continue
        if live_score(a2) < LIVE_HIT:
            return False
    return True


# ---------------- 拟人化输入 ----------------

_MOTIONEVENT_OK = None


def probe_motionevent():
    """探测 motionevent 是否可用(只读 usage, 不注入以免误触暂停), 不可用则回退 swipe。"""
    global _MOTIONEVENT_OK
    if _MOTIONEVENT_OK is not None:
        return _MOTIONEVENT_OK
    r = adb("shell", "input", timeout=20)
    blob = (r.stdout + r.stderr).decode("utf-8", "ignore")
    low = blob.lower()
    if not blob or "error" in low or "no devices" in low:
        # 设备离线/命令报错: 结果不确定, 不缓存, 待设备在线后重探
        return False
    _MOTIONEVENT_OK = "motionevent" in blob
    log("输入原语: %s" % ("motionevent 折线" if _MOTIONEVENT_OK else "swipe 直线(回退)"))
    return _MOTIONEVENT_OK


def human_swipe(short=False):
    """拟人化上滑。

    折线轨迹 + smoothstep 速度曲线 + 横向漂移; 按 73ms/点开销反算 sleep,
    使设备端真实滑动时长贴近目标。点数随时长自适应(点数越多最短时长越长)。
    """
    if short:
        x0 = random.randint(470, 640)
        y0 = random.randint(1480, 1760)
        dist = random.randint(120, 400)
    else:
        x0 = random.randint(420, 680)
        y0 = random.randint(1600, 1880)
        dist = random.randint(880, 1380)
    x1 = x0 + random.randint(-50, 50)
    y1 = max(360, y0 - dist)
    dur = int(ln(math.log(300), 0.34, 120, 780))

    if not probe_motionevent():
        adb("shell", "input swipe %d %d %d %d %d" % (x0, y0, x1, y1, dur))
        return

    n = 3 if dur < 260 else (4 if dur < 430 else (5 if dur < 620 else 6))
    amp = random.uniform(6, 30)
    phase = random.uniform(0, math.tau)
    # 每段可用 sleep = (目标时长 - 点数开销) / 段数, 下限 25ms
    per = max(0.025, (dur - n * CMD_OVERHEAD_MS) / 1000.0 / (n - 1))
    parts = []
    for i in range(n):
        t = i / (n - 1.0)
        e = t * t * (3 - 2 * t)          # smoothstep: 先加速后减速
        x = x0 + (x1 - x0) * e + amp * math.sin(math.pi * t) * math.sin(phase)
        y = y0 + (y1 - y0) * e
        kind = "DOWN" if i == 0 else ("UP" if i == n - 1 else "MOVE")
        parts.append("input motionevent %s %d %d" % (kind, int(round(x)), int(round(y))))
        if i < n - 1:
            parts.append("sleep %.3f" % (per * random.uniform(0.82, 1.22)))
    adb("shell", "; ".join(parts), timeout=40)


def look_back():
    """回看: 下滑一小段, 模拟真人往回翻。"""
    x0 = random.randint(470, 610)
    y0 = random.randint(700, 1000)
    y1 = y0 + random.randint(560, 980)
    dur = int(ln(math.log(320), 0.30, 160, 650))
    adb("shell", "input swipe %d %d %d %d %d" % (
        x0, y0, x0 + random.randint(-30, 30), y1, dur))


def like():
    """点赞: 带按压时长的单点点击, 避免零时长 tap 的瞬时特征。"""
    x = 988 + random.randint(-6, 6)
    y = 1225 + random.randint(-8, 8)
    press = random.randint(38, 95)
    adb("shell", "input motionevent DOWN %d %d; sleep %.3f; input motionevent UP %d %d"
        % (x, y, press / 1000.0, x, y))


def quick_swipe(times=2, gap=(0.55, 0.95)):
    """连续干净上滑(不带半滑/回看/点赞), 用于退出直播间后立刻离开当前内容。"""
    for i in range(times):
        human_swipe()
        if i < times - 1:
            time.sleep(random.uniform(*gap))


def window_count():
    try:
        out = adb("shell", "dumpsys", "window", "windows").stdout.decode("utf-8", "ignore")
        return len(re.findall(r"Window #\d+ Window\{", out))
    except Exception:
        return 0


def close_popups():
    """仅在窗口数异常时才 dump(减少 accessibility_enabled 翻转), 命中关键词则点击。"""
    r = adb("shell", "uiautomator", "dump", "--compressed", "/sdcard/_w.xml")
    if b"dumped" not in r.stdout:
        return False
    tmp_xml = os.path.join(TMP, "_w.xml")
    adb("pull", "/sdcard/_w.xml", tmp_xml)
    adb("shell", "rm", "/sdcard/_w.xml")
    try:
        x = io.open(tmp_xml, encoding="utf-8").read()
    except Exception:
        return False
    keys = ["关闭", "开心收下", "开心领取", "立即领取", "领取奖励", "继续赚钱",
            "知道了", "放弃", "以后再说", "暂不"]
    for mm in re.finditer(r"<node[^>]*>", x):
        s = mm.group(0)
        t = re.search(r'text="([^"]*)"', s)
        d = re.search(r'content-desc="([^"]*)"', s)
        bnd = re.search(r'bounds="([^"]*)"', s)
        val = ((t.group(1) if t else "") + " " + (d.group(1) if d else "")).strip()
        if val and bnd and any(k in val for k in keys):
            m2 = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bnd.group(1))
            if m2:
                a1, b1, a2, b2 = map(int, m2.groups())
                cx, cy = (a1 + a2) // 2, (b1 + b2) // 2
                log("popup: " + val[:14] + " @ " + str((cx, cy)))
                adb("shell", "input motionevent DOWN %d %d; sleep 0.06; input motionevent UP %d %d"
                    % (cx, cy, cx, cy))
                time.sleep(random.uniform(1.0, 2.0))
                return True
    return False


# ---------------- 行为模型 ----------------

def pick_dwell():
    """单条视频停留时长(秒)。对数正态混合, 重尾且带快速划过/深度观看两类极值。"""
    if FAST:
        # 刷金币模式: 里程只认"切换视频"后的有效浏览, 长停留不计时。
        # 压到 2.2~9 秒(均值约 5s), 保持轻微随机避免机械感。
        return ln(math.log(5.0), 0.28, 2.2, 9.0)
    r = random.random()
    if r < 0.06:
        return ln(math.log(3.2), 0.38, 1.2, 7.0)      # 快速划过
    if r < 0.16:
        return ln(math.log(7.0), 0.42, 3.0, 18.0)     # 略看
    if r < 0.86:
        return ln(math.log(15.0), 0.52, 6.0, 48.0)    # 常规
    return ln(math.log(62.0), 0.42, 35.0, 180.0)      # 深度观看


def rest(seconds, end_time):
    seconds = min(seconds, max(0.0, end_time - time.time() - 20))
    if seconds < 5:
        return
    log("  休息 %.0fs" % seconds)
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(min(4.0, max(0.1, seconds - (time.time() - t0))))
    log("  休息结束")


ON_TICK = None
FAST = False


def main(minutes=30.0, on_tick=None, fast=False):
    """拟人化观看循环。

    on_tick: 每轮开头调用; 返回 True 表示本轮已由外部处理(如广告页/弹窗),
             跳过常规观看逻辑。供 dy_coin.py 复用。
    fast:    刷金币模式, 压缩停留/休息时长(里程只认切换视频后的有效浏览)。
    """
    global ON_TICK, FAST
    ON_TICK = on_tick
    FAST = fast
    end = time.time() + minutes * 60
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    log("=== 抖音拟人化观看 %.0f 分钟 (直播间 %.0fs 上滑) ===" % (minutes, LIVE_DWELL))
    ensure_device()
    probe_motionevent()

    swipes = 0
    live_skips = 0
    likes = 0
    last_dev = 0
    last_lost_log = 0
    last_popup = 0
    win_base = window_count()
    # 环静止超过该阈值即认为不是进度环(装饰环), 改用时长驱动
    RING_STATIC = 5.0
    next_rest = random.randint(22, 42)
    _last_hook = 0.0

    base0 = time.time()
    last_arc = -1
    last_change = time.time()
    ring_target = None      # 环驱动: 目标弧长(角度)
    time_target = None      # 时长驱动: 目标秒数

    def reset(tag):
        nonlocal base0, last_arc, last_change, ring_target, time_target
        log("  本条 %.0f 秒 [%s]" % (time.time() - base0, tag))
        base0 = time.time()
        last_arc = -1
        last_change = time.time()
        ring_target = None
        time_target = None

    def do_swipe(tag, clean=False):
        nonlocal swipes, next_rest, likes, live_skips
        # 犹豫半滑: 真人常见的"滑了一半又停住"
        if not clean and random.random() < 0.08:
            human_swipe(short=True)
            log("  半滑(犹豫)")
            time.sleep(ln(math.log(1.2), 0.35, 0.4, 3.0))
        # 回看: 往回翻一条
        if not clean and random.random() < 0.05:
            look_back()
            log("  回看")
            time.sleep(ln(math.log(1.6), 0.35, 0.6, 3.0))
        # 点赞: 停留越久越可能点
        dwell = time.time() - base0
        p_like = 0.06 if dwell < 6 else (0.16 if dwell < 20 else 0.26)
        if not clean and random.random() < p_like:
            like()
            likes += 1
            log("  点赞")
            time.sleep(ln(math.log(1.1), 0.30, 0.5, 2.5))
        human_swipe()
        swipes += 1
        if tag == "直播间":
            live_skips += 1
        log(">> %s上滑 (#%d)" % ("" if tag == "直播间" else tag + " ", swipes))
        if FAST:
            # 刷金币模式: 滑后只作短暂稳定, 尽快进入下一条
            time.sleep(ln(math.log(1.6), 0.28, 0.8, 2.8))
        else:
            time.sleep(ln(math.log(4.2), 0.32, 2.0, 11.0))
        reset(tag)
        if swipes >= next_rest:
            if FAST:
                next_rest = swipes + random.randint(60, 110)
                rest(ln(math.log(8), 0.35, 4, 14), end)
            else:
                next_rest = swipes + random.randint(22, 42)
                rest(ln(math.log(55), 0.45, 25, 150), end)

    while time.time() < end:
        try:
            if time.time() - last_dev > 30:
                if not ensure_device():
                    if time.time() - last_lost_log > 30:
                        log("设备掉线，等待重连(需在手机上开启无线调试)")
                        last_lost_log = time.time()
                    time.sleep(5)
                    continue
                last_dev = time.time()

            if ON_TICK is not None and time.time() - _last_hook > 8:
                _last_hook = time.time()
                if ON_TICK():
                    reset("hook")
                    continue

            f = focus()
            if PKG not in f:
                if "systemui" in f or "launcher" in f:
                    time.sleep(1)
                    continue
                log("非抖音 focus=%s" % f[:50])
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
                time.sleep(random.uniform(1.5, 2.5))
                if PKG not in focus():
                    adb("shell", "monkey", "-p", PKG, "-c",
                        "android.intent.category.LAUNCHER", "1")
                    time.sleep(6)
                reset("切换")
                continue

            # 误入直播间(独立 Activity LivePlayActivity): 立刻 BACK 退出,
            # 退出后不再等判据, 直接干净上滑两下离开当前内容
            if "LivePlayActivity" in f or ".live.Live" in f:
                t_exit = time.time()
                log("误入直播间，立即退出")
                for _ in range(4):
                    adb("shell", "input", "keyevent", "KEYCODE_BACK")
                    left = False
                    for _ in range(6):          # 最多等 1.5s 让退场动画走完再判
                        time.sleep(0.25)
                        if "LivePlayActivity" not in focus():
                            left = True
                            break
                    if left:
                        break
                quick_swipe(2)
                swipes += 2
                log(">> 退出直播间后连续上滑两下 (#%d), 退出耗时 %.1fs"
                    % (swipes, time.time() - t_exit))
                reset("退出直播间")
                continue

            a = grab()
            if a is None:
                time.sleep(1)
                continue

            # 误触进入 UP 主主页: 立即 BACK 退出, 回到 feed 后直接干净上滑两下划走
            if is_profile(a):
                t_exit = time.time()
                log("误入UP主主页，立即退出")
                left = False
                for _ in range(3):
                    adb("shell", "input", "keyevent", "KEYCODE_BACK")
                    time.sleep(0.3)
                    b = grab()
                    if b is None:
                        continue
                    if not is_profile(b):
                        left = is_feed(b)
                        break
                if left:
                    quick_swipe(2)
                    swipes += 2
                    log(">> 主页退出后连续上滑两下 (#%d), 退出耗时 %.1fs"
                        % (swipes, time.time() - t_exit))
                else:
                    log(">> 主页已退出 (%.1fs)" % (time.time() - t_exit))
                reset("退出主页")
                continue

            # 直播间预览卡片优先: 3 秒上滑, 不按红包视频规则久留
            if live_score(a) >= LIVE_HIT:
                log("直播间 停留%.1fs" % LIVE_DWELL)
                if live_watch(LIVE_DWELL):
                    do_swipe("直播间", clean=True)
                    continue
                log("  直播间已划走")

            arc, _R = ring_arc(a)
            rf = redfrac(a)
            age = time.time() - base0

            if rf > 0.4:
                # 环在涨 -> 由环驱动; 环静止(装饰环) -> 由重尾时长驱动
                if arc > last_arc + 1:
                    last_arc = arc
                    last_change = time.time()
                stall = time.time() - last_change
                log("红包圈=%d 停=%.0f 停留=%.0f" % (arc, stall, age))
                if stall < RING_STATIC:
                    if ring_target is None:
                        # 需环接近填满才划走(保守), 否则交由 stall 兜底走时长驱动
                        ring_target = random.uniform(0.70, 0.98) * 360
                    if arc >= ring_target and age > 5:
                        do_swipe("环")
                        continue
                else:
                    if time_target is None:
                        time_target = pick_dwell()
                    if age >= time_target:
                        do_swipe("时长")
                        continue
            else:
                if time_target is None:
                    time_target = pick_dwell()
                if age >= time_target:
                    do_swipe("无红包")
                    continue

            # 弹窗: 窗口数异常才 dump, 频率 70~130s 抖动
            if time.time() - last_popup > random.uniform(70, 130):
                last_popup = time.time()
                if window_count() > win_base + 1:
                    close_popups()

            time.sleep(random.uniform(0.8, 2.0) if FAST else random.uniform(1.2, 3.2))
        except Exception as e:
            log("异常已忽略: %s" % repr(e)[:80])
            time.sleep(2)

    log("=== 结束 共上滑 %d 次 (直播间 %d, 点赞 %d) ===" % (swipes, live_skips, likes))


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 30)
