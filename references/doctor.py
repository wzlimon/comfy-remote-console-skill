#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""doctor.py - environment check for comfy-mobile-studio.

Run:  .venv\Scripts\python.exe doctor.py
Output is plain ASCII on purpose (Windows console encoding).
Paste the output back to get exact next steps.
"""

import os
import sys
import json
import socket

BASE = os.path.dirname(os.path.abspath(__file__))


def ok(m):
    print("[ OK ] " + m)


def wn(m):
    print("[WARN] " + m)


def er(m):
    print("[FAIL] " + m)


def port_open(host, port, timeout=1.5):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


print("=" * 56)
print(" comfy-mobile-studio  doctor")
print(" base: " + BASE)
print("=" * 56)

print("\n[1] Python")
v = sys.version_info
print("     version: %d.%d.%d" % (v.major, v.minor, v.micro))
if v.major == 3 and v.minor >= 10:
    ok("Python version OK")
else:
    er("Need Python 3.10+")

print("\n[2] Virtual env")
venv_py = os.path.join(BASE, ".venv", "Scripts", "python.exe")
if os.path.isfile(venv_py):
    ok(".venv exists")
else:
    er(".venv missing  ->  python -m venv .venv")

print("\n[3] Packages")
for m in ("flask", "requests", "yaml", "waitress"):
    try:
        __import__(m)
        ok(m)
    except ImportError:
        er(m + " not installed")

print("\n[4] config.yaml")
cfg_path = os.path.join(BASE, "config.yaml")
cfg = None
if not os.path.isfile(cfg_path):
    er("config.yaml missing  ->  copy config.example.yaml to config.yaml")
else:
    ok("config.yaml exists")
    try:
        import yaml
        cfg = yaml.safe_load(open(cfg_path, encoding="utf-8"))
        ok("YAML parsed")
    except Exception as e:
        er("YAML parse error: %s" % e)

if cfg:
    pw = (cfg.get("server") or {}).get("password", "") or ""
    if (not pw) or ("改成" in pw) or ("change" in pw.lower()) or ("CHANGEME" in pw):
        er("password is still a placeholder - MUST change before exposing")
    elif len(pw) < 8:
        wn("password too short (<8 chars)")
    else:
        ok("password set (len=%d)" % len(pw))
    cu = cfg.get("comfyui") or {}
    print("     comfyui: %s:%s" % (cu.get("host"), cu.get("port")))
    wfs = cu.get("workflows") or {}
    print("     workflows in config: %d" % len(wfs))
    for name, w in wfs.items():
        inj = w.get("inject", {}) or {}
        pn = inj.get("prompt_node", "") or "-"
        print("       %-9s file=%-26s prompt_node=%s" % (name, w.get("file"), pn))

print("\n[5] workflows/*.json")
wdir = os.path.join(BASE, "workflows")
if os.path.isdir(wdir):
    files = sorted(f for f in os.listdir(wdir) if f.endswith(".json"))
    print("     files: %d" % len(files))
    if not files:
        er("no workflow json  ->  export API format from ComfyUI")
    for f in files:
        p = os.path.join(wdir, f)
        try:
            d = json.load(open(p, encoding="utf-8"))
            if not isinstance(d, dict):
                wn("%s unexpected structure" % f)
            elif "nodes" in d or "links" in d:
                er("%s is UI format - re-export as API format" % f)
            else:
                ok("%-24s API format (%d nodes)" % (f, len(d)))
        except Exception as e:
            er("%s parse error: %s" % (f, e))
else:
    er("workflows/ dir missing")

print("\n[6] ComfyUI 127.0.0.1:8188")
if port_open("127.0.0.1", 8188):
    ok("ComfyUI reachable")
else:
    er("ComfyUI NOT reachable - start ComfyUI first")

print("\n[7] Console 127.0.0.1:8790")
if port_open("127.0.0.1", 8790):
    ok("console running")
else:
    wn("console not running  ->  .venv\\Scripts\\python.exe run_server.py")

print("\n[8] H3_DIRECTOR_TOKEN")
tok = os.environ.get("H3_DIRECTOR_TOKEN")
if tok:
    ok("token in current env (len=%d)" % len(tok))
else:
    found = False
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
        val, _ = winreg.QueryValueEx(k, "H3_DIRECTOR_TOKEN")
        ok("token in HKCU Environment (len=%d)" % len(val))
        found = True
    except Exception:
        pass
    if not found:
        wn("token not set - needed for API / CODEX uploads")

print("\n" + "=" * 56)
print(" paste this whole output back for exact next steps")
print("=" * 56)
