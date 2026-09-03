"""手机网页控制台：表单提交 -> 本机 ComfyUI 生成 -> 超分 -> 网页取片。

手机连同一个 WiFi，浏览器打开 http://<本机IP>:8790 就能用。
启动：双击「启动.bat」，或 python server.py
"""

from __future__ import annotations

import functools
import logging
import mimetypes
import re
import secrets
import socket
import time
import uuid
import os
from pathlib import Path
from urllib.parse import quote

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    request,
    send_file,
    send_from_directory,
    session,
)

from core.comfy import ComfyError
from core.config import load_config, setup_logging
from core.pipeline import Pipeline
from core.store import (
    STATUS_QUEUED,
    TaskStore,
    to_public,
)

LOG = logging.getLogger("server")

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"

# 允许作为参考图 / 上传文件的图片后缀（按需求限定为 png/jpg/jpeg/webp）
ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".webp"}
# 项目名白名单：仅允许字母/数字/下划线/连字符，杜绝路径穿越
PROJECT_RE = re.compile(r"^[\w-]+$")
# 从路径推断素材类型时识别的已知类别关键字
KNOWN_TYPES = {
    "characters", "environments", "props", "backgrounds", "effects",
    "costumes", "vehicles", "weapons", "faces", "scenes", "creatures",
    "items", "locations", "concepts", "refs", "reference",
}
MODES = {"t2v": "文生视频", "i2v": "图生视频", "flf": "首尾帧", "r2v": "万能参考", "t2i": "文生图"}

cfg = load_config()
log_file = setup_logging(cfg)

store = TaskStore(cfg.path("runtime.db_file", "data/tasks.db"))
stale = store.mark_stale_as_failed()
if stale:
    LOG.warning("上次残留 %s 条未完成任务，已标记为失败", stale)

pipeline = Pipeline(cfg, store)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = (
    int(cfg.get("server.max_upload_mb", 30)) * 1024 * 1024
)

# 会话密钥持久化，重启后手机不用重新输密码
_secret_file = BASE_DIR / "data" / "secret.key"
if _secret_file.exists():
    app.secret_key = _secret_file.read_bytes()
else:
    _secret_file.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    _secret_file.write_bytes(key)
    app.secret_key = key

app.permanent_session_lifetime = 60 * 60 * 24 * int(
    cfg.get("server.session_days", 30)
)

PASSWORD = str(cfg.get("server.password", "") or "")

# 导演令牌：给远端 CODEX / 自动化调度用的「无 cookie」认证。
# 通过环境变量 H3_DIRECTOR_TOKEN 注入（不要写进 config.yaml，避免入库），
# 调用方在请求头带  Authorization: Bearer <token>  即可绕过网页密码。
DIRECTOR_TOKEN = os.environ.get("H3_DIRECTOR_TOKEN", "") or ""


# ---------------------------------------------------------------- 鉴权

def is_director_request() -> bool:
    """请求是否携带有效的导演令牌（Bearer）。用于 API / 自动化调度。"""
    if not DIRECTOR_TOKEN:
        return False
    auth = request.headers.get("Authorization", "")
    # compare_digest 防时序侧信道；Bearer 后有一个空格
    return secrets.compare_digest(auth, f"Bearer {DIRECTOR_TOKEN}")


def need_login(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if PASSWORD and not session.get("ok") and not is_director_request():
            return jsonify({"error": "need_login"}), 401
        return fn(*a, **kw)

    return wrapper


@app.post("/api/login")
def api_login():
    if not PASSWORD:
        return jsonify({"ok": True})
    data = request.get_json(silent=True) or {}
    if str(data.get("password", "")) == PASSWORD:
        session.permanent = True
        session["ok"] = True
        return jsonify({"ok": True})
    time.sleep(1)  # 挡一下暴力尝试
    return jsonify({"ok": False, "error": "密码不对"}), 403


@app.get("/api/me")
def api_me():
    logged_in = bool(not PASSWORD or session.get("ok") or is_director_request())
    return jsonify({"need_password": bool(PASSWORD), "logged_in": logged_in})


@app.post("/api/logout")
def api_logout():
    # 清掉网页登录态（cookie session）；导演令牌走 Authorization 头，不在此处处理。
    # 退出后 need_login 重新生效，下次访问会要求重新输密码。
    session.pop("ok", None)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- 页面

@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/web/<path:name>")
def web_asset(name: str):
    return send_from_directory(WEB_DIR, name)


@app.get("/favicon.ico")
def favicon():
    return ("", 204)


# ---------------------------------------------------------------- 选项

@app.get("/api/options")
@need_login
def api_options():
    """表单要用的所有选项，全部从 config.yaml 读，改配置即改界面."""
    wf_opts = cfg.get("comfyui.workflow_options", {}) or {}
    default_wf = cfg.get("comfyui.workflow_default", "turbo")

    workflows = []
    for label, name in wf_opts.items():
        inj = cfg.get(f"comfyui.workflows.{name}.inject", {}) or {}
        workflows.append({
            "name": name,
            "label": label,
            "steps_options": inj.get("steps_options") or [],
            "steps_default": inj.get("steps_default"),
        })

    first = next(iter(wf_opts.values()), "standard")
    inj = cfg.get(f"comfyui.workflows.{first}.inject", {}) or {}

    # 每个模式下可选的「流程」档位（标准 / Turbo），用于前端过滤芯片。
    # 只保留「确实在 workflows 里配置过」的名称，避免指到不存在的工作流。
    mode_wf_cfg = cfg.get("comfyui.mode_workflows", {}) or {}
    configured = set(wf_opts.values())
    mode_workflows = {
        m: [n for n in (mode_wf_cfg.get(m) or []) if n in configured]
        for m in MODES
    }

    return jsonify({
        "workflows": workflows,
        "workflow_default": default_wf,
        "mode_workflows": mode_workflows,
        "ratios": list((inj.get("ratio_options") or {}).keys()),
        "ratio_default": "9:16 竖屏",
        "resolutions": list((inj.get("resolution_options") or {}).keys()),
        "resolution_default": "480P",
        "durations": [4, 5, 6, 8, 10, 12, 15, 20, 25, 30],
        "duration_default": inj.get("duration_default", 5),
        "modes": MODES,
        "upscale_enabled": bool(cfg.get("topaz.enabled", True)),
        "netdisk_enabled": bool(cfg.get("baidu.enabled", True)),
    })


# ---------------------------------------------------------------- 图库

def safe_upload_path(rel: str) -> Path | None:
    """把 uploads 相对路径解析成绝对路径，越界 / 非法 / 非图片一律返回 None。

    防穿越：禁止 ``..``、绝对路径、盘符（冒号）；解析后必须仍落在 uploads_dir 内；
    且只接受白名单图片后缀。用于 /upload 路由与 ref 名称解析。
    """
    if not rel or ".." in rel or rel.startswith("/") or ":" in rel:
        return None
    cand = (pipeline.uploads_dir / rel).resolve()
    try:
        cand.relative_to(pipeline.uploads_dir.resolve())
    except ValueError:
        return None
    if cand.suffix.lower() not in ALLOWED_IMG:
        return None
    if not cand.is_file():
        return None
    return cand


def infer_type(rel: str) -> str:
    """从相对路径推断素材类型（characters / environments / props 等）。"""
    parts = Path(rel).parts
    for s in parts:
        if s.lower() in KNOWN_TYPES:
            return s.lower()
    if len(parts) >= 2:
        return parts[1].lower()
    return "other"


def build_ref_item(p: Path, rel: str, project: str) -> dict:
    """构造 /api/refs 单条素材结构（name / rel_path / project / type / url）。"""
    url = "/upload/" + "/".join(quote(seg) for seg in Path(rel).parts)
    return {
        "name": p.name,
        "rel_path": rel,
        "project": project,
        "type": infer_type(rel),
        "url": url,
        "size": p.stat().st_size,
        "ts": int(p.stat().st_mtime),
    }


@app.get("/api/refs")
@need_login
def api_refs():
    """已上传图片清单（项目专库）。

    ?project=xxx 只返回该项目目录（递归子目录）下的素材；不带 project 则只返回
    uploads 根目录下的平铺文件（向后兼容旧行为）。
    """
    project = (request.args.get("project") or "").strip()
    items: list[dict] = []
    if project:
        if not PROJECT_RE.match(project):
            return jsonify({"error": "非法的项目名（仅允许字母/数字/下划线/连字符）"}), 400
        root = pipeline.uploads_dir / project
        if root.is_dir():
            for p in root.rglob("*"):
                if p.is_file() and p.suffix.lower() in ALLOWED_IMG:
                    rel = p.relative_to(pipeline.uploads_dir).as_posix()
                    items.append(build_ref_item(p, rel, project))
    else:
        d = pipeline.uploads_dir
        if d.exists():
            for p in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if p.is_file() and p.suffix.lower() in ALLOWED_IMG:
                    items.append(build_ref_item(p, p.name, ""))
    items.sort(key=lambda x: x["ts"], reverse=True)
    return jsonify({"refs": items, "project": project})


# ---------------------------------------------------------------- 程序化上传（自动化 / CODEX）

def _safe_subdir(sub: str) -> Path | None:
    """校验并清洗「子目录」（项目内或 uploads 内的相对路径），越界/非法返回 None。

    禁止 ``..``、绝对路径、盘符（冒号）；解析后必须仍落在 uploads_dir 内。
    """
    if not sub:
        return Path(".")
    if ".." in sub or sub.startswith("/") or ":" in sub:
        return None
    # 只取各段、丢弃任何段内的路径分隔符，避免客户端在文件名里夹带路径
    parts = [p for p in Path(sub).parts if p not in ("", ".", "..")]
    cand = Path(*parts) if parts else Path(".")
    try:
        # 相对 uploads_dir 必须为相对路径（不在其外）
        cand.relative_to(Path("."))  # 恒为真，仅占位以统一类型
    except ValueError:
        return None
    return cand


@app.post("/api/upload")
@need_login
def api_upload():
    """给自动化 / CODEX 用的批量资产上传接口（与网页上传共用同样的校验与安全边界）。

    认证：Bearer 导演令牌（Authorization: Bearer <H3_DIRECTOR_TOKEN>）或网页登录 cookie。

    表单字段：
      - files:      一个或多个图片文件（必填，至少 1 个）
      - project:    项目名（可选，正则 ^[\\w-]+$）；不填则落到 uploads 根目录（向后兼容）
      - subdir:     项目内（或无项目时 uploads 内）的相对子目录，如 characters/model_sheets；
                    留空则直接放在项目根。禁止 .. / 绝对路径 / 盘符。

    返回：{ uploaded: [{name, rel_path, project, type, url, size}], errors: [...] }
    落盘位置：D:\\comfy-mobile-studio\\data\\uploads\\<project>\\<subdir>\\<文件名>
    落盘后前端图库（?project=xxx 递归扫描）立即可见，可直接在 /api/submit 里用 rel_path 引用。
    """
    project = (request.form.get("project") or "").strip()
    if project and not PROJECT_RE.match(project):
        return jsonify({"error": "非法的项目名（仅允许字母 / 数字 / 下划线 / 连字符）"}), 400

    subdir = _safe_subdir((request.form.get("subdir") or "").strip())
    if subdir is None:
        return jsonify({"error": "非法的 subdir（禁止 .. / 绝对路径 / 盘符）"}), 400

    files = request.files.getlist("files")
    if not files:
        # 兼容单个文件字段名 file
        f = request.files.get("file")
        if f and f.filename:
            files = [f]
    if not files:
        return jsonify({"error": "没有收到文件（请用 files 字段，支持多文件）"}), 400

    # 目标目录
    if project:
        dest_dir = pipeline.uploads_dir / project / subdir
    else:
        dest_dir = pipeline.uploads_dir / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    uploaded: list[dict] = []
    errors: list[dict] = []

    for fs in files:
        raw_name = fs.filename or ""
        name = Path(raw_name).name  # 只取文件名，丢弃客户端夹带的路径
        if not name:
            errors.append({"filename": raw_name, "error": "空文件名"})
            continue
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_IMG:
            errors.append({"filename": name, "error": f"不支持的图片格式 {ext or '(未知)'}，限 png/jpg/jpeg/webp"})
            continue
        # 自动化批量上传：同名即覆盖（确定性、不产生 _xxxxxx 重复副本）。
        # 调用端重跑脚本也能得到一致的最终状态，不会越攒越多。
        dst = dest_dir / name
        try:
            fs.save(dst)
        except Exception as exc:  # pragma: no cover - 磁盘/权限异常
            errors.append({"filename": name, "error": f"保存失败: {exc}"})
            continue
        rel = dst.relative_to(pipeline.uploads_dir).as_posix()
        item = build_ref_item(dst, rel, project)
        uploaded.append(item)

    return jsonify({"uploaded": uploaded, "errors": errors})


# ---------------------------------------------------------------- 提交

def _save_upload(file_storage, tag: str, project: str = "") -> str:
    """存手机上传的图片，返回相对 uploads 的路径（项目库会带子目录前缀）。"""
    ext = Path(file_storage.filename or "").suffix.lower()
    if ext not in ALLOWED_IMG:
        raise ValueError(f"不支持的图片格式 {ext or '(未知)'}，请用 jpg / png / webp")
    name = f"{time.strftime('%Y%m%d_%H%M%S')}_{tag}_{uuid.uuid4().hex[:8]}{ext}"
    dst_dir = pipeline.uploads_dir / project if project else pipeline.uploads_dir
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / name
    file_storage.save(dst)
    return (Path(project) / name).as_posix() if project else name


def _resolve_img(file_field: str, name_field: str, tag: str, project: str = "") -> str | None:
    """取一张输入图：优先复用图库里已存在的（可传根文件名，也可传项目相对路径），
    否则接收本次新上传的文件。返回相对 uploads 的路径，或 None（都没给）。

    安全：name_field 若含 ``..`` / 绝对路径 / 盘符，或落点不在 uploads 内，或不是白名单
    图片，一律当无效并回退到新上传（绝不把外部路径拼进系统目录）。
    """
    nm = (request.form.get(name_field) or "").strip()
    if nm:
        p = safe_upload_path(nm)
        if p:
            return Path(nm).as_posix()
    fs = request.files.get(file_field)
    if fs and fs.filename:
        return _save_upload(fs, tag, project)
    return None


@app.post("/api/submit")
@need_login
def api_submit():
    f = request.form
    prompt = (f.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "提示词不能为空"}), 400

    mode = (f.get("mode") or "t2v").strip()
    if mode not in MODES:
        return jsonify({"error": f"未知模式 {mode}"}), 400

    # 项目专库：project 非空时，参考图引用、上传落盘、成品输出都按项目隔离。
    # 仅允许字母/数字/下划线/连字符，杜绝路径穿越。
    project = (f.get("project") or "").strip()
    if project and not PROJECT_RE.match(project):
        return jsonify({
            "error": "非法的项目名（仅允许字母 / 数字 / 下划线 / 连字符）"
        }), 400

    # 万能参考：普通版用 r2v 工作流；若选了 Turbo 档（workflow=turbo/r2vt），
    # 则切到带 MiniMax-H3 Turbo LoRA 的 r2vt 工作流加速。
    if mode == "r2v":
        wf_pick = (f.get("workflow") or "").strip()
        workflow = "r2vt" if wf_pick in ("turbo", "r2vt") else "r2v"
    elif mode == "t2i":
        # 文生图走 zimage 工作流（产出图片，不超分）
        workflow = "zimage"
    else:
        workflow = (f.get("workflow") or cfg.get("comfyui.workflow_default", "turbo")).strip()

    def _int(key, default=None):
        raw = (f.get(key) or "").strip()
        if not raw:
            return default
        try:
            return int(float(raw))
        except ValueError:
            return default

    first_name = last_name = ""
    ref_names: list[str] = []
    try:
        if mode in ("i2v", "flf"):
            first_name = _resolve_img("first_image", "first_image_name", "first", project)
            if not first_name:
                return jsonify({"error": "这个模式需要上传首帧图片"}), 400
        if mode == "flf":
            last_name = _resolve_img("last_image", "last_image_name", "last", project)
            if not last_name:
                return jsonify({"error": "首尾帧模式需要再传一张尾帧图片"}), 400
        if mode == "r2v":
            # 最多 3 张参考图，至少 1 张；支持图库复用（传根文件名或项目相对路径）或新上传
            for i in range(3):
                r = _resolve_img(f"ref_{i}", f"ref_{i}_name", f"ref{i}", project)
                if r:
                    ref_names.append(r)
            if not ref_names:
                return jsonify({"error": "万能参考需要至少上传 1 张参考图"}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # 图生 / 首尾帧 模式下宽高跟随图片，比例选项无意义，记录成空。
    # 万能参考（r2v/r2vt）仍由 ResolutionSelector 控制输出比例，比例照常生效。
    ratio = "" if mode in ("i2v", "flf") else (f.get("ratio") or "").strip()

    task_id = store.create(
        mode=mode,
        workflow=workflow,
        prompt=prompt,
        negative=(f.get("negative") or "").strip(),
        ratio=ratio,
        duration=_int("duration", 5),
        resolution=(f.get("resolution") or "480P").strip(),
        steps=_int("steps"),
        seed=_int("seed"),
        upscale=1 if (f.get("upscale") or "1") in ("1", "true", "on") else 0,
        first_image=first_name,
        last_image=last_name,
        refs="|".join(ref_names),
        project=project,
        status=STATUS_QUEUED,
        stage_text="排队中",
    )
    ahead = pipeline.submit(task_id)
    LOG.info("网页提交任务 #%s（%s，项目=%s），前面排 %s 个",
             task_id, mode, project or "默认", ahead)
    return jsonify({"ok": True, "id": task_id, "ahead": ahead})


# ---------------------------------------------------------------- 查询

@app.get("/api/tasks")
@need_login
def api_tasks():
    limit = min(int(request.args.get("limit", 20)), 200)
    offset = max(int(request.args.get("offset", 0)), 0)
    keyword = (request.args.get("keyword") or "").strip()
    status = (request.args.get("status") or "").strip()

    rows = store.list(limit=limit, offset=offset, keyword=keyword, status=status)
    return jsonify({
        "tasks": [to_public(r) for r in rows],
        "total": store.count(keyword=keyword, status=status),
        "actives": [to_public(r) for r in store.actives()],
        "queue": pipeline.queue_info(),
        "stats": store.stats(),
    })


@app.get("/api/task/<int:task_id>")
@need_login
def api_task(task_id: int):
    row = store.get(task_id)
    if not row:
        return jsonify({"error": "记录不存在"}), 404
    return jsonify(to_public(row))


@app.post("/api/task/<int:task_id>/cancel")
@need_login
def api_cancel(task_id: int):
    msg = pipeline.cancel(task_id)
    return jsonify({"ok": True, "message": msg})


@app.post("/api/task/<int:task_id>/delete")
@need_login
def api_delete(task_id: int):
    row = store.delete(task_id)
    if not row:
        return jsonify({"error": "记录不存在"}), 404
    # 成品 / 封面与本任务强绑定，直接清（支持项目专库的相对路径）
    for key in ("result_file", "thumb_file"):
        name = row.get(key)
        if name:
            p = pipeline.resolve_asset(name)
            if p:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
    # 上传图（首帧/尾帧/参考图）是共享资产：只有没被其它任务引用时才删，
    # 否则可能误删别人正在复用的图
    names: set[str] = set()
    for k in ("first_image", "last_image"):
        if row.get(k):
            names.add(row[k])
    for n in (row.get("refs") or "").split("|"):
        if n:
            names.add(n)
    for n in names:
        if not store.image_used_elsewhere(n, task_id):
            try:
                (pipeline.uploads_dir / n).unlink(missing_ok=True)
            except OSError:
                pass
    return jsonify({"ok": True})


@app.get("/api/health")
@need_login
def api_health():
    return jsonify(pipeline.health())


@app.get("/api/export.csv")
@need_login
def api_export():
    csv_text = store.export_csv()
    return Response(
        "\ufeff" + csv_text,  # BOM，Excel 打开不乱码
        mimetype="text/csv",
        headers={
            "Content-Disposition": (
                f"attachment; filename=video_records_"
                f"{time.strftime('%Y%m%d_%H%M%S')}.csv"
            )
        },
    )


# ---------------------------------------------------------------- 文件

@app.get("/video/<path:name>")
@need_login
def get_video(name: str):
    """成品视频。支持 Range，手机上可以边下边播、也可以点下载存相册."""
    p = pipeline.resolve_asset(name)
    if not p:
        abort(404)
    dl = request.args.get("dl") == "1"
    resp = send_file(
        str(p), conditional=True,
        mimetype=mimetypes.guess_type(name)[0] or "video/mp4",
        as_attachment=dl, download_name=Path(name).name if dl else None,
    )
    resp.headers["Accept-Ranges"] = "bytes"
    return resp


@app.get("/image/<path:name>")
@need_login
def get_image(name: str):
    """文生图成品图片（视频封面也复用此路由）。"""
    p = pipeline.resolve_asset(name)
    if not p:
        abort(404)
    dl = request.args.get("dl") == "1"
    resp = send_file(
        str(p), conditional=True,
        mimetype=mimetypes.guess_type(name)[0] or "image/png",
        as_attachment=dl, download_name=Path(name).name if dl else None,
    )
    resp.headers["Accept-Ranges"] = "bytes"
    return resp


@app.get("/thumb/<path:name>")
@need_login
def get_thumb(name: str):
    p = pipeline.resolve_asset(name)
    if not p:
        abort(404)
    return send_file(str(p), conditional=True)


@app.get("/upload/<path:name>")
@need_login
def get_upload(name: str):
    """图库参考图。强制校验落点在 uploads 内且为白名单图片，防穿越与任意文件读取。"""
    p = safe_upload_path(name)
    if not p:
        abort(404)
    return send_file(str(p), conditional=True)


# ---------------------------------------------------------------- 错误处理

@app.errorhandler(413)
def too_large(_exc):
    mb = cfg.get("server.max_upload_mb", 30)
    return jsonify({"error": f"图片太大了，上限 {mb} MB"}), 413


@app.errorhandler(ComfyError)
def comfy_error(exc):
    return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------- 启动

def local_ips() -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def _ensure_dirs() -> None:
    """克隆后首次运行确保 data 子目录存在，避免数据库/落盘报错."""
    for key in ("runtime.results_dir", "runtime.uploads_dir", "runtime.thumbs_dir"):
        try:
            cfg.path(key).mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("无法创建目录 %s: %s", key, exc)


def main() -> None:
    _ensure_dirs()
    host = str(cfg.get("server.host", "0.0.0.0"))
    port = int(cfg.get("server.port", 8790))

    print("=" * 60)
    print("  ComfyUI 手机控制台已启动")
    print("=" * 60)
    for ip in local_ips():
        print(f"  手机浏览器打开：  http://{ip}:{port}")
    print(f"  本机浏览器打开：  http://127.0.0.1:{port}")
    print(f"  访问密码：        {'已设置' if PASSWORD else '未设置（仅建议局域网使用）'}")
    print(f"  导演令牌(API调度): {'已启用' if DIRECTOR_TOKEN else '未设置（仅网页密码登录）'}")
    print(f"  日志文件：        {log_file}")
    print("=" * 60)
    print("  关掉这个窗口即停止服务")
    print()

    try:
        from waitress import serve

        serve(app, host=host, port=port, threads=8, channel_timeout=600)
    except ImportError:
        app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
