#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""版本自检 / 自动更新。

任何 agent 接手本项目, **会话开始时第一件事就是跑这个**:

    python update.py            # 报告当前版本, 落后则 git pull --ff-only
    python update.py --check    # 只检查, 不拉取

退出码: 0 = 已是最新 / 已更新成功 / 离线(可继续), 1 = 不在 git 仓库或 pull 失败。

拉取后如果 `dy_coin.py` / `dy_auto.py` 有变化, 需要重启正在跑的脚本才会生效:
    taskkill /F /IM python.exe ; del coin.lock
    python dy_coin.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def sh(*args):
    return subprocess.run(["git", "-C", HERE] + list(args),
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def read_version():
    p = os.path.join(HERE, "VERSION")
    if not os.path.exists(p):
        return "?"
    with open(p, encoding="utf-8") as f:
        return f.read().strip() or "?"


def main():
    check_only = "--check" in sys.argv
    ver = read_version()

    r = sh("rev-parse", "--short", "HEAD")
    if r.returncode != 0:
        print("[版本] 不是 git 仓库(或还没有提交), VERSION=%s, 跳过更新" % ver)
        return 1

    head = r.stdout.strip()
    br = sh("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    print("[版本] VERSION=%s  commit=%s  branch=%s" % (ver, head, br))

    f = sh("fetch", "--all", "--quiet")
    if f.returncode != 0:
        print("[版本] git fetch 失败(离线可继续): %s" % f.stderr.strip()[:200])
        return 0

    up = sh("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if up.returncode != 0:
        print("[版本] 当前分支没有上游, 跳过")
        return 0

    behind = sh("rev-list", "--count", "HEAD..@{u}").stdout.strip()
    if behind in ("", "0"):
        print("[版本] 已是最新")
        return 0

    print("[版本] 落后上游 %s 个提交" % behind)
    if check_only:
        print("[版本] --check: 未拉取")
        return 0

    p = sh("pull", "--ff-only")
    out = (p.stdout.strip() + "\n" + p.stderr.strip()).strip()
    print(out[:2000])
    if p.returncode != 0:
        print("[版本] 自动拉取失败, 手动处理: git pull --ff-only")
        return 1
    print("[版本] 已更新到 %s" % sh("rev-parse", "--short", "HEAD").stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
