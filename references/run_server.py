#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""启动引导：从 Windows 用户环境变量(HKCU\Environment)读取 H3_DIRECTOR_TOKEN 后拉起 server.py。

为什么需要它：
  许多「工具/终端」启动的 shell 不会继承 User 作用域的环境变量，导致 server.py
  拿不到 DIRECTOR_TOKEN，API/CODEX 用 Bearer 调会 401。本脚本在拉起 server 前
  主动从注册表读令牌注入环境，彻底规避这个问题。

用法：启动.bat 内部调用本脚本；也可直接 `python run_server.py`。
"""
import os
import subprocess
import sys
import winreg

TOKEN = ""
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        TOKEN, _ = winreg.QueryValueEx(k, "H3_DIRECTOR_TOKEN")
except Exception as e:  # noqa
    print(f"[warn] 读取 H3_DIRECTOR_TOKEN 失败（API/CODEX 将无法用 Bearer 调用）: {e}", file=sys.stderr)

BASE = os.path.dirname(os.path.abspath(__file__))
os.environ["H3_DIRECTOR_TOKEN"] = TOKEN or ""

py = os.path.join(BASE, ".venv", "Scripts", "python.exe")
if not os.path.exists(py):
    py = sys.executable  # 兜底用当前解释器
server = os.path.join(BASE, "server.py")

print(f"[run_server] token_len={len(TOKEN or '')}, cwd={BASE}")
p = subprocess.Popen([py, server], cwd=BASE, env=os.environ.copy())
try:
    p.wait()
except KeyboardInterrupt:
    p.terminate()
