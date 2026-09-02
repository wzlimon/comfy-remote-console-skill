#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""CODEX / 自动化批量上传素材到资产库（纯标准库，无需装依赖）。

用法:
  python upload_assets.py --project p1 --subdir characters/model_sheets chr.png "scene_*.png"
  python upload_assets.py --server http://192.168.1.50:8790 --token "xxxx" --project p1 item.png

令牌优先级: --token > 环境变量 H3_DIRECTOR_TOKEN > Windows 用户环境变量(HKCU\Environment)
安全: 同名覆盖（确定性）；仅 png/jpg/jpeg/webp；project 限 [A-Za-z0-9_-]；subdir 禁 .. / 绝对路径 / 盘符
"""
import argparse
import base64
import glob
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request

try:
    import winreg
    _HAS_WINREG = True
except Exception:  # noqa
    _HAS_WINREG = False


def resolve_token(explicit):
    if explicit:
        return explicit
    env = os.environ.get("H3_DIRECTOR_TOKEN", "")
    if env:
        return env
    if _HAS_WINREG:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                v, _ = winreg.QueryValueEx(k, "H3_DIRECTOR_TOKEN")
                return v or ""
        except Exception:  # noqa
            pass
    return ""


def upload_one(server, token, project, subdir, path):
    fn = os.path.basename(path)
    ctype = mimetypes.guess_type(fn)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        data = f.read()
    boundary = "----codexbatch" + os.urandom(6).hex()
    parts = []
    if project:
        parts.append(f"--{boundary}".encode())
        parts.append(b'Content-Disposition: form-data; name="project"')
        parts.append(b""); parts.append(project.encode())
    if subdir:
        parts.append(f"--{boundary}".encode())
        parts.append(b'Content-Disposition: form-data; name="subdir"')
        parts.append(b""); parts.append(subdir.encode())
    parts.append(f"--{boundary}".encode())
    parts.append(f'Content-Disposition: form-data; name="files"; filename="{fn}"'.encode())
    parts.append(f"Content-Type: {ctype}".encode())
    parts.append(b""); parts.append(data)
    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    body = b"\r\n".join(parts)

    req = urllib.request.Request(server.rstrip("/") + "/api/upload", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8','ignore')}"}


def main():
    ap = argparse.ArgumentParser(description="批量上传素材到资产库")
    ap.add_argument("files", nargs="+", help="图片文件（支持通配符）")
    ap.add_argument("--server", default="http://127.0.0.1:8790")
    ap.add_argument("--project", default="", help="项目名（专库），留空落 uploads 根")
    ap.add_argument("--subdir", default="", help="项目内相对子目录")
    ap.add_argument("--token", default=None, help="导演令牌，缺省自动从环境/注册表读取")
    args = ap.parse_args()

    token = resolve_token(args.token)
    if not token:
        print("[warn] 未找到 H3_DIRECTOR_TOKEN，若服务端要求令牌将返回 401", file=sys.stderr)

    paths = []
    for pat in args.files:
        paths.extend(sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else [pat])
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        print("没有可上传的文件"); return 1

    ok = fail = 0
    for p in paths:
        res = upload_one(args.server, token, args.project, args.subdir, p)
        if "error" in res:
            fail += 1; print(f"[FAIL] {p}: {res['error']}"); continue
        up = res.get("uploaded", [])
        for e in res.get("errors", []):
            fail += 1; print(f"[FAIL] {e.get('filename')}: {e.get('error')}")
        if up:
            ok += 1
            print(f"[OK]   {p} -> {up[0].get('rel_path')}  ({up[0].get('url')})")
    print(f"\n完成: 成功 {ok}，失败 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
