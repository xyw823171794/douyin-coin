# -*- coding: utf-8 -*-
"""环境自检。换新电脑/新手机后先跑这个, 比直接跑主脚本省时间。

    python check.py

逐项检查: 依赖 -> adb -> 设备 -> 分辨率 -> 抖音是否安装 -> 当前页面。
任何一项 FAIL 都会给出对应的修复建议。
"""
import sys

try:                        # Windows 控制台默认 GBK, 强制 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OK, BAD, WARN = "[ OK ]", "[FAIL]", "[WARN]"
fails = 0


def item(state, title, detail=""):
    global fails
    if state == BAD:
        fails += 1
    print("%s %-14s %s" % (state, title, detail))


def main():
    # 1. 依赖
    try:
        import numpy
        import PIL
        item(OK, "numpy", numpy.__version__)
        item(OK, "Pillow", PIL.__version__)
    except Exception as e:
        item(BAD, "依赖", "%s -> pip install -r requirements.txt" % e)
        return 1

    # 2. adb
    import dy_env
    import os
    if not (os.path.exists(dy_env.ADB) or dy_env.ADB == "adb"):
        item(BAD, "adb", "找不到: %s -> 设环境变量 ADB_BIN" % dy_env.ADB)
        return 1
    r = dy_env.adb(["version"], timeout=15)
    ver = r.stdout.decode("utf-8", "ignore").splitlines()
    item(OK if r.returncode == 0 else BAD, "adb",
         "%s | %s" % (dy_env.ADB, ver[0] if ver else ""))

    # 3. 设备
    devs = dy_env.list_devices()
    if not devs:
        item(BAD, "设备", "无在线设备 -> 开 USB 调试并授权, 或 adb connect IP:PORT")
        return 1
    if len(devs) > 1:
        item(WARN, "设备", "多台在线 %s -> 已选 %s (可用 ADB_SERIAL 指定)"
             % (devs, dy_env.SERIAL))
    else:
        item(OK, "设备", devs[0])

    # 4. 分辨率
    size = dy_env.screen_size()
    if size is None:
        item(WARN, "分辨率", "读取失败")
    elif size == (1080, 2400):
        item(OK, "分辨率", "1080x2400 (与坐标标定一致)")
    else:
        item(BAD, "分辨率", "%dx%d != 1080x2400 -> 所有坐标需重标定, "
             "或 FORCE_RES=1 强行运行" % size)

    # 5. 抖音
    pm = dy_env.adb(["shell", "pm", "list", "packages",
                     "com.ss.android.ugc.aweme.lite"], timeout=30)
    if b"aweme.lite" in pm.stdout:
        item(OK, "抖音极速版", "已安装")
    else:
        item(BAD, "抖音极速版", "未安装 (包名 com.ss.android.ugc.aweme.lite)")

    # 6. 当前焦点
    out = dy_env.adb(["shell", "dumpsys", "window"], timeout=30).stdout
    foc = ""
    for ln in out.decode("utf-8", "ignore").splitlines():
        if "mCurrentFocus" in ln and "null" not in ln:
            foc = ln.split("=", 1)[-1].strip()
    item(OK if "aweme" in foc else WARN, "当前窗口", foc or "读不到")

    print()
    if fails:
        print("有 %d 项 FAIL, 按上面提示修复后重跑 check.py" % fails)
        return 1
    print("自检通过。可以运行: python dy_coin.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
