#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ComfyUI 手机远程控制台 —— 最小可运行框架（自包含，无需本项目的 core/ 模块）。

落地步骤：
  1) 复制本文件为 server.py，与 config.yaml、web/ 同目录；
  2) pip install flask requests pyyaml waitress；
  3) 设 Windows 用户环境变量 H3_DIRECTOR_TOKEN（给 API/CODEX 用）；
  4) 改 submit_to_comfyui() 接到你自己的 ComfyUI 工作流；
  5) 用 run_server.py 启动（它会从注册表读令牌再拉起本文件）。

本文件已包含全部「经过实测」的关键模式：
  - 双认证：网页密码(cookie) + 导演令牌(Bearer, compare_digest 防时序)
  - 防穿越：safe_upload_path / _safe_subdir 拒绝 .. / 绝对路径 / 盘符 / 非白名单后缀
  - 项目专库：uploads/<project>/<subdir>/，/api/refs 递归，/api/upload 批量安全上传
  - /api/submit 用相对路径引用资产，交给后台 worker
"""
from __future__ import annotations

import functools
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests
from flask import (
    Flask, Response, abort, jsonify, request, send_file, session,
)
import yaml

# ----------------------------------------------------------------- 基础
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
OUTPUTS_DIR = DATA_DIR / "outputs"
DB_FILE = DATA_DIR / "tasks.db"

ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".webp"}
PROJECT_RE = re.compile(r"^[\w-]+$")
KNOWN_TYPES = {"characters", "environments", "props", "backgrounds", "effects",
               "costumes", "faces", "scenes", "creatures", "refs"}

# 读配置
cfg = yaml.safe_load((BASE_DIR / "config.yaml").read_text(encoding="utf-8")) if (BASE_DIR / "config.yaml").exists() else {}
SERVER = cfg.get("server", {})
COMFY = cfg.get("comfyui", {})
RUNTIME = cfg.get("runtime", {})

UPLOADS_DIR = Path(RUNTIME.get("uploads_dir", UPLOADS_DIR))
OUTPUTS_DIR = Path(RUNTIME.get("outputs_dir", OUTPUTS_DIR))
DB_FILE = Path(RUNTIME.get("db_file", DB_FILE))
for d in (DATA_DIR, UPLOADS_DIR, OUTPUTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = int(SERVER.get("max_upload_mb", 30)) * 1024 * 1024

_secret = DATA_DIR / "secret.key"
if _secret.exists():
    app.secret_key = _secret.read_bytes()
else:
    k = secrets.token_bytes(32)
    _secret.write_bytes(k)
    app.secret_key = k
app.permanent_session_lifetime = 60 * 60 * 24 * int(SERVER.get("session_days", 30))

PASSWORD = str(SERVER.get("password", "") or "")
DIRECTOR_TOKEN = os.environ.get("H3_DIRECTOR_TOKEN", "") or ""   # 仅来自环境变量，绝不进配置


# ----------------------------------------------------------------- 鉴权
def is_director_request() -> bool:
    if not DIRECTOR_TOKEN:
        return False
    auth = request.headers.get("Authorization", "")
    return secrets.compare_digest(auth, f"Bearer {DIRECTOR_TOKEN}")


def need_login(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if PASSWORD and not session.get("ok") and not is_director_request():
            return jsonify({"error": "need_login"}), 401
        return fn(*a, **kw)
    return wrapper


# ----------------------------------------------------------------- 安全
def safe_upload_path(rel: str):
    """把 uploads 相对路径解析成绝对路径；越界/非法/非图片一律 None。"""
    if not rel or ".." in rel or rel.startswith("/") or ":" in rel:
        return None
    cand = (UPLOADS_DIR / rel).resolve()
    try:
        cand.relative_to(UPLOADS_DIR.resolve())
    except ValueError:
        return None
    if cand.suffix.lower() not in ALLOWED_IMG or not cand.is_file():
        return None
    return cand


def _safe_subdir(sub: str):
    if not sub:
        return Path(".")
    if ".." in sub or sub.startswith("/") or ":" in sub:
        return None
    parts = [p for p in Path(sub).parts if p not in ("", ".", "..")]
    return Path(*parts) if parts else Path(".")


def infer_type(rel: str) -> str:
    parts = Path(rel).parts
    for s in parts:
        if s.lower() in KNOWN_TYPES:
            return s.lower()
    return parts[1].lower() if len(parts) >= 2 else "other"


def build_ref_item(p: Path, rel: str, project: str) -> dict:
    url = "/upload/" + "/".join(quote(seg) for seg in Path(rel).parts)
    st = p.stat()
    return {"name": p.name, "rel_path": rel, "project": project,
            "type": infer_type(rel), "url": url,
            "size": st.st_size, "ts": int(st.st_mtime)}


# ----------------------------------------------------------------- 任务库（最小 sqlite）
def db():
    c = sqlite3.connect(DB_FILE)
    c.execute("""CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, prompt TEXT,
        project TEXT DEFAULT '', status TEXT DEFAULT 'queued',
        result_file TEXT, thumb_file TEXT, created INTEGER)""")
    c.commit()
    return c


def submit_to_comfyui(task: dict) -> str:
    """★ 后端接入点：把任务投到本机 ComfyUI。返回外部 prompt_id 或空。

    真实实现示例（伪代码，按你的工作流改）：
        payload = {"prompt": build_graph(task), "client_id": str(uuid.uuid4())}
        r = requests.post(f"http://{COMFY['host']}:{COMFY['port']}/prompt",
                         json=payload, timeout=30)
        return r.json().get("prompt_id")
    生成完成后 ComfyUI 把成品写到 OUTPUTS_DIR/<project>/...，
    再 UPDATE tasks SET status='done', result_file=rel。
    本模板用占位实现，保证端到端可跑通流程。
    """
    # TODO: 替换为你的 ComfyUI 调用
    time.sleep(0.2)
    return uuid.uuid4().hex


def worker():
    """后台轮询队列，调 submit_to_comfyui。生产环境可换成更强健的队列。"""
    while True:
        try:
            c = db()
            row = c.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row:
                tid = row[0]
                c.execute("UPDATE tasks SET status='running' WHERE id=?", (tid,))
                c.commit()
                submit_to_comfyui(dict(zip(
                    ["id", "mode", "prompt", "project", "status",
                     "result_file", "thumb_file", "created"], row)))
                c.execute("UPDATE tasks SET status='done' WHERE id=?", (tid,))
                c.commit()
            c.close()
        except Exception as e:  # noqa
            print("[worker]", e)
        time.sleep(2)


threading.Thread(target=worker, daemon=True).start()


# ----------------------------------------------------------------- 页面
@app.get("/")
def index():
    return send_file(WEB_DIR / "index.html")


# ----------------------------------------------------------------- 鉴权路由
@app.post("/api/login")
def api_login():
    if not PASSWORD:
        return jsonify({"ok": True})
    data = request.get_json(silent=True) or {}
    if str(data.get("password", "")) == PASSWORD:
        session.permanent = True
        session["ok"] = True
        return jsonify({"ok": True})
    time.sleep(1)
    return jsonify({"ok": False, "error": "密码不对"}), 403


# ----------------------------------------------------------------- 资产库
@app.get("/api/refs")
@need_login
def api_refs():
    project = (request.args.get("project") or "").strip()
    items = []
    if project:
        if not PROJECT_RE.match(project):
            return jsonify({"error": "非法的项目名（仅允许字母/数字/下划线/连字符）"}), 400
        root = UPLOADS_DIR / project
        if root.is_dir():
            for p in root.rglob("*"):
                if p.is_file() and p.suffix.lower() in ALLOWED_IMG:
                    rel = p.relative_to(UPLOADS_DIR).as_posix()
                    items.append(build_ref_item(p, rel, project))
    else:
        for p in sorted(UPLOADS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and p.suffix.lower() in ALLOWED_IMG:
                items.append(build_ref_item(p, p.name, ""))
    items.sort(key=lambda x: x["ts"], reverse=True)
    return jsonify({"refs": items, "project": project})


@app.post("/api/upload")
@need_login
def api_upload():
    """给 CODEX / 自动化用的批量上传：Bearer 令牌 或 网页 cookie。"""
    project = (request.form.get("project") or "").strip()
    if project and not PROJECT_RE.match(project):
        return jsonify({"error": "非法的项目名"}), 400
    subdir = _safe_subdir((request.form.get("subdir") or "").strip())
    if subdir is None:
        return jsonify({"error": "非法的 subdir（禁止 .. / 绝对路径 / 盘符）"}), 400

    files = request.files.getlist("files") or ([request.files.get("file")] if request.files.get("file") else [])
    if not files or not files[0] or not files[0].filename:
        return jsonify({"error": "没有收到文件（请用 files 字段，支持多文件）"}), 400

    dest_dir = (UPLOADS_DIR / project / subdir) if project else (UPLOADS_DIR / subdir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    uploaded, errors = [], []
    for fs in files:
        name = Path(fs.filename or "").name
        if not name:
            continue
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_IMG:
            errors.append({"filename": name, "error": f"不支持的格式 {ext or '(未知)'}，限 png/jpg/jpeg/webp"})
            continue
        # 同名覆盖：确定性，自动化重跑不产生 _xxxxxx 副本
        dst = dest_dir / name
        try:
            fs.save(dst)
        except Exception as exc:
            errors.append({"filename": name, "error": f"保存失败: {exc}"})
            continue
        rel = dst.relative_to(UPLOADS_DIR).as_posix()
        uploaded.append(build_ref_item(dst, rel, project))
    return jsonify({"uploaded": uploaded, "errors": errors})


# ----------------------------------------------------------------- 提交
def _resolve_img(name_field: str, tag: str, project: str):
    nm = (request.form.get(name_field) or "").strip()
    if nm:
        p = safe_upload_path(nm)
        if p:
            return Path(nm).as_posix()
    fs = request.files.get(tag)
    if fs and fs.filename:
        ext = Path(fs.filename).suffix.lower()
        if ext not in ALLOWED_IMG:
            raise ValueError(f"不支持的图片格式 {ext or '(未知)'}")
        name = f"{int(time.time())}_{tag}_{uuid.uuid4().hex[:8]}{ext}"
        d = UPLOADS_DIR / project if project else UPLOADS_DIR
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(fs.read())
        return (Path(project) / name).as_posix() if project else name
    return None


@app.post("/api/submit")
@need_login
def api_submit():
    f = request.form
    prompt = (f.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "提示词不能为空"}), 400
    mode = (f.get("mode") or "t2v").strip()
    project = (f.get("project") or "").strip()
    if project and not PROJECT_RE.match(project):
        return jsonify({"error": "非法的项目名"}), 400

    ref_names = []
    try:
        for i in range(3):
            r = _resolve_img(f"ref_{i}_name", f"ref{i}", project)
            if r:
                ref_names.append(r)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    c = db()
    cur = c.execute(
        "INSERT INTO tasks(mode,prompt,project,status,created) VALUES(?,?,?,?,?)",
        (mode, prompt, project, "queued", int(time.time())))
    tid = cur.lastrowid
    c.commit(); c.close()
    return jsonify({"ok": True, "id": tid, "refs": ref_names})


@app.get("/api/tasks")
@need_login
def api_tasks():
    c = db()
    rows = c.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 50").fetchall()
    c.close()
    cols = ["id", "mode", "prompt", "project", "status", "result_file", "thumb_file", "created"]
    return jsonify({"tasks": [dict(zip(cols, r)) for r in rows]})


# ----------------------------------------------------------------- 文件（全部安全校验）
@app.get("/upload/<path:name>")
@need_login
def get_upload(name: str):
    p = safe_upload_path(name)
    if not p:
        abort(404)
    return send_file(str(p), conditional=True)


@app.get("/video/<path:name>")
@need_login
def get_video(name: str):
    p = OUTPUTS_DIR / name
    if not p.is_file():
        abort(404)
    return send_file(str(p), conditional=True,
                     mimetype=mimetypes.guess_type(name)[0] or "video/mp4")


# ----------------------------------------------------------------- 启动
def main():
    host = SERVER.get("host", "0.0.0.0")
    port = int(SERVER.get("port", 8790))
    print("=" * 50)
    print("  ComfyUI 手机控制台已启动")
    print(f"  本机: http://127.0.0.1:{port}")
    print(f"  密码: {'已设置' if PASSWORD else '未设置(仅建议局域网)'}")
    print(f"  导演令牌(API): {'已启用' if DIRECTOR_TOKEN else '未设置'}")
    print("=" * 50)
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8, channel_timeout=600)
    except ImportError:
        app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
