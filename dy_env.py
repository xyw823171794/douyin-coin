# -*- coding: utf-8 -*-
"""环境探测: adb 路径 / 设备序列号 / 屏幕分辨率。

被 dy_coin.py 和 dy_auto.py 共用。目标: 换一台电脑、换一根线、
换有线/无线, 都不用改代码。

优先级:
  adb 路径   ADB_BIN 环境变量  >  PATH 里的 adb  >  常见安装位置
  设备序列号  ADB_SERIAL 环境变量  >  ANDROID_SERIAL  >  唯一在线设备
"""
import os
import re
import shutil
import subprocess
import sys

WINDOWS = os.name == "nt"

# 无参数时用来兜底的常见 adb 位置(按顺序找, 找到第一个存在的)
_CANDIDATES = [
    os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
    os.path.expandvars(r"%USERPROFILE%\AppData\Local\Android\Sdk\platform-tools\adb.exe"),
    r"C:\Program Files\Eagly\data\adb.exe",
    r"C:\platform-tools\adb.exe",
    r"C:\adb\adb.exe",
    os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
    "/usr/local/bin/adb",
    "/usr/bin/adb",
    "/opt/homebrew/bin/adb",
    "/opt/android-sdk/platform-tools/adb",
]


def find_adb():
    """返回可执行的 adb 路径; 找不到返回 'adb' 让调用方报错。"""
    env = os.environ.get("ADB_BIN")
    if env and os.path.exists(env):
        return env
    p = shutil.which("adb")
    if p:
        return p
    for c in _CANDIDATES:
        if c and os.path.exists(c):
            return c
    return "adb"


ADB = find_adb()
SERIAL = None       # 由 pick_serial() 在文件末尾填充


def adb(args, timeout=30, serial=None):
    """执行 adb 命令, 自动带上设备序列号(避免多设备时报错)。"""
    cmd = [ADB]
    s = serial if serial is not None else SERIAL
    if s:
        cmd += ["-s", s]
    cmd += list(args)
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:
        return subprocess.CompletedProcess(cmd, 1, b"", b"")


def list_devices():
    """返回在线设备序列号列表。"""
    out = adb(["devices"], timeout=15).stdout.decode("utf-8", "ignore")
    return [ln.split("\t")[0].strip() for ln in out.splitlines()
            if "\tdevice" in ln]


def pick_serial():
    """确定要操作的设备。多设备时优先 USB(无冒号)的设备。"""
    env = os.environ.get("ADB_SERIAL") or os.environ.get("ANDROID_SERIAL")
    if env:
        return env
    devs = list_devices()
    if not devs:
        return None
    if len(devs) == 1:
        return devs[0]
    usb = [d for d in devs if ":" not in d]
    return (usb or devs)[0]


SERIAL = pick_serial()


def screen_size(serial=None):
    """返回 (w, h); 取不到返回 None。"""
    out = adb(["shell", "wm", "size"], timeout=15, serial=serial).stdout
    m = re.search(rb"(\d+)x(\d+)", out)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def describe():
    return "adb=%s  serial=%s  size=%s  python=%s" % (
        ADB, SERIAL, screen_size(), sys.version.split()[0])


if __name__ == "__main__":
    print(describe())
