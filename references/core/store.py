"""生成记录存储（SQLite）。

每一条提交都留档：提示词、参数、状态、耗时、成品路径。
网页的「历史」页从这里读，也可以一键导出 CSV 备查。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

# 任务状态流转：queued -> running -> upscaling -> done / failed / canceled
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_UPSCALING = "upscaling"
STATUS_DELIVERING = "delivering"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"

ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING, STATUS_UPSCALING, STATUS_DELIVERING)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    REAL NOT NULL,
    started_at    REAL,
    finished_at   REAL,
    status        TEXT NOT NULL,
    stage_text    TEXT DEFAULT '',
    mode          TEXT NOT NULL,          -- t2v / i2v / flf（首尾帧）/ r2v（万能参考）
    workflow      TEXT NOT NULL,          -- standard / turbo / r2v / zimage
    media_type    TEXT DEFAULT 'video',   -- video（视频）/ image（文生图）
    prompt        TEXT NOT NULL,
    negative      TEXT DEFAULT '',
    ratio         TEXT DEFAULT '',
    duration      INTEGER,
    resolution    TEXT DEFAULT '',
    steps         INTEGER,
    seed          INTEGER,
    upscale       INTEGER DEFAULT 1,      -- 是否超分
    first_image   TEXT DEFAULT '',        -- 本地上传文件名（图生/首尾帧）
    last_image    TEXT DEFAULT '',
    refs          TEXT DEFAULT '',        -- 万能参考：参考图文件名，用 | 分隔
    project       TEXT DEFAULT '',        -- 项目专库：空=默认库；非空=项目子目录名
    prompt_id     TEXT DEFAULT '',        -- ComfyUI 的任务号
    raw_video     TEXT DEFAULT '',        -- 超分前原片路径
    result_file   TEXT DEFAULT '',        -- 成品文件名（在 results 目录内）
    netdisk_path  TEXT DEFAULT '',        -- 网盘内路径
    thumb_file    TEXT DEFAULT '',        -- 封面图文件名
    elapsed       REAL,
    error         TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""

# 允许写入的列，防止手滑写错列名
_FIELDS = {
    "created_at", "started_at", "finished_at", "status", "stage_text", "mode",
    "workflow", "media_type", "prompt", "negative", "ratio", "duration", "resolution", "steps",
    "seed", "upscale", "first_image", "last_image", "refs", "project", "prompt_id", "raw_video",
    "result_file", "netdisk_path", "thumb_file", "elapsed", "error",
}


class TaskStore:
    """任务记录库。SQLite + 一把写锁，够用且不会丢数据."""

    def __init__(self, db_file: Path) -> None:
        self.db_file = Path(db_file)
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_file, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(SCHEMA)
            # 兼容旧库：缺列则补上（refs / media_type / project 是后加的）
            cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()
            }
            for col, typ in (
                ("refs", "TEXT DEFAULT ''"),
                ("media_type", "TEXT DEFAULT 'video'"),
                ("project", "TEXT DEFAULT ''"),
            ):
                if col not in cols:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {typ}")
        LOG.info("记录库就绪: %s", self.db_file)

    # ---------------- 写 ----------------

    def create(self, **kw: Any) -> int:
        """新建一条任务记录，返回任务 ID."""
        data = {k: v for k, v in kw.items() if k in _FIELDS}
        data.setdefault("created_at", time.time())
        data.setdefault("status", STATUS_QUEUED)
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"INSERT INTO tasks ({cols}) VALUES ({marks})", list(data.values())
            )
            return int(cur.lastrowid)

    def update(self, task_id: int, **kw: Any) -> None:
        """更新任务字段."""
        data = {k: v for k, v in kw.items() if k in _FIELDS}
        if not data:
            return
        sets = ", ".join(f"{k}=?" for k in data)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE tasks SET {sets} WHERE id=?",
                list(data.values()) + [task_id],
            )

    def delete(self, task_id: int) -> dict[str, Any] | None:
        """删除记录，返回被删的行（调用方可顺手清文件）."""
        row = self.get(task_id)
        if not row:
            return None
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return row

    def image_used_elsewhere(self, name: str, except_id: int) -> bool:
        """判断某张上传图是否还被其它任务引用（首帧/尾帧/参考图）。

        用于删除任务时决定是否清理图片：被复用中的图不能删，否则会误删
        别人正在用的参考图。
        """
        if not name:
            return False
        with self._connect() as conn:
            for col in ("first_image", "last_image"):
                if conn.execute(
                    f"SELECT 1 FROM tasks WHERE {col}=? AND id<>?",
                    (name, except_id),
                ).fetchone():
                    return True
            rows = conn.execute(
                "SELECT refs FROM tasks WHERE id<>?", (except_id,)
            ).fetchall()
            for (refs,) in rows:
                if refs and name in [x for x in refs.split("|") if x]:
                    return True
        return False

    def mark_stale_as_failed(self) -> int:
        """服务重启时，把上次残留的「进行中」标成失败，避免界面上永远转圈."""
        with self._lock, self._connect() as conn:
            marks = ", ".join("?" for _ in ACTIVE_STATUSES)
            cur = conn.execute(
                f"UPDATE tasks SET status=?, error=? "
                f"WHERE status IN ({marks})",
                [STATUS_FAILED, "服务重启，任务中断"] + list(ACTIVE_STATUSES),
            )
            return cur.rowcount or 0

    # ---------------- 读 ----------------

    def get(self, task_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        keyword: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        args: list[Any] = []
        if keyword:
            sql += " AND prompt LIKE ?"
            args.append(f"%{keyword}%")
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def count(self, keyword: str = "", status: str = "") -> int:
        sql = "SELECT COUNT(*) AS c FROM tasks WHERE 1=1"
        args: list[Any] = []
        if keyword:
            sql += " AND prompt LIKE ?"
            args.append(f"%{keyword}%")
        if status:
            sql += " AND status=?"
            args.append(status)
        with self._connect() as conn:
            return int(conn.execute(sql, args).fetchone()["c"])

    def actives(self) -> list[dict[str, Any]]:
        """所有还在进行中的任务（排队 + 生成 + 超分）."""
        marks = ", ".join("?" for _ in ACTIVE_STATUSES)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE status IN ({marks}) ORDER BY id",
                list(ACTIVE_STATUSES),
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]
            done = conn.execute(
                "SELECT COUNT(*) c FROM tasks WHERE status=?", (STATUS_DONE,)
            ).fetchone()["c"]
            failed = conn.execute(
                "SELECT COUNT(*) c FROM tasks WHERE status=?", (STATUS_FAILED,)
            ).fetchone()["c"]
            avg = conn.execute(
                "SELECT AVG(elapsed) a FROM tasks WHERE status=? AND elapsed IS NOT NULL",
                (STATUS_DONE,),
            ).fetchone()["a"]
        return {
            "total": int(total),
            "done": int(done),
            "failed": int(failed),
            "avg_elapsed": round(float(avg), 1) if avg else 0.0,
        }

    def export_csv(self) -> str:
        """导出全部记录为 CSV 文本（Excel 可直接打开）."""
        rows = self.list(limit=100000)
        buf = io.StringIO()
        cols = [
            "id", "created_at", "status", "mode", "workflow", "prompt", "ratio",
            "duration", "resolution", "steps", "seed", "upscale", "result_file",
            "netdisk_path", "elapsed", "error",
        ]
        writer = csv.writer(buf)
        writer.writerow([
            "编号", "提交时间", "状态", "模式", "流程", "提示词", "比例", "时长(秒)",
            "分辨率", "步数", "种子", "已超分", "成品文件", "网盘路径", "耗时(秒)", "错误",
        ])
        for r in rows:
            line = []
            for c in cols:
                v = r.get(c)
                if c == "created_at" and v:
                    v = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(v)))
                if c == "elapsed" and v:
                    v = round(float(v), 1)
                line.append("" if v is None else v)
            writer.writerow(line)
        return buf.getvalue()


def to_public(row: dict[str, Any]) -> dict[str, Any]:
    """把数据库行转成前端要的精简结构."""
    return {
        "id": row["id"],
        "created_at": row.get("created_at"),
        "created_text": time.strftime(
            "%m-%d %H:%M", time.localtime(float(row["created_at"]))
        ) if row.get("created_at") else "",
        "status": row.get("status"),
        "stage_text": row.get("stage_text") or "",
        "mode": row.get("mode"),
        "workflow": row.get("workflow"),
        "media_type": row.get("media_type") or "video",
        "prompt": row.get("prompt") or "",
        "ratio": row.get("ratio") or "",
        "duration": row.get("duration"),
        "resolution": row.get("resolution") or "",
        "steps": row.get("steps"),
        "seed": row.get("seed"),
        "upscale": bool(row.get("upscale")),
        "result_file": row.get("result_file") or "",
        "thumb_file": row.get("thumb_file") or "",
        "netdisk_path": row.get("netdisk_path") or "",
        "elapsed": round(float(row["elapsed"]), 1) if row.get("elapsed") else None,
        "error": row.get("error") or "",
        "has_first": bool(row.get("first_image")),
        "has_last": bool(row.get("last_image")),
        "refs": (row.get("refs") or "").split("|") if row.get("refs") else [],
    }
