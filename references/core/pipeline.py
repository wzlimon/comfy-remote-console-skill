"""任务流水线：排队 -> ComfyUI 生成 -> Topaz 超分 -> 成品落地 -> 网盘备份。

单线程串行执行（一张显卡，同时只跑一个任务才不会爆显存）。
Web 端只负责把任务丢进队列，剩下的都在这里跑。
"""

from __future__ import annotations

import logging
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .comfy import ComfyClient, ComfyError
from .config import Config
from .delivery import NetdiskDelivery
from .store import (
    STATUS_CANCELED,
    STATUS_DELIVERING,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_UPSCALING,
    TaskStore,
)
from .topaz import TopazError, TopazUpscaler

LOG = logging.getLogger(__name__)

ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]+')

# 文生图产物扩展名（产出图片时走图片交付分支，不超分）
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def safe_slug(text: str, max_len: int = 24) -> str:
    """把提示词压成能当文件名的短串."""
    s = ILLEGAL.sub("", (text or "").strip())
    s = re.sub(r"\s+", "_", s)
    return s[:max_len] or "video"


class Pipeline:
    """生成流水线。一个后台线程串行消费队列."""

    def __init__(self, cfg: Config, store: TaskStore) -> None:
        self.cfg = cfg
        self.store = store

        self.comfy = ComfyClient(cfg)
        self.topaz = TopazUpscaler(cfg)
        self.delivery = (
            NetdiskDelivery(cfg) if cfg.get("baidu.enabled", True) else None
        )

        self.results_dir = cfg.path("runtime.results_dir", "data/results")
        self.uploads_dir = cfg.path("runtime.uploads_dir", "data/uploads")
        self.thumbs_dir = cfg.path("runtime.thumbs_dir", "data/thumbs")
        self.outputs_dir = cfg.path("runtime.outputs_dir", "data/outputs")
        for d in (self.results_dir, self.uploads_dir, self.thumbs_dir, self.outputs_dir):
            d.mkdir(parents=True, exist_ok=True)

        keep = cfg.get("runtime.keep_source_dir") or ""
        self.keep_source_dir = Path(str(keep)) if keep else None

        self._ffmpeg = str(cfg.get("topaz.ffmpeg", "") or "")

        self._q: queue.Queue[int] = queue.Queue()
        self._lock = threading.Lock()
        self._current_id: int | None = None
        self._current_pid: str = ""
        self._cancel_ids: set[int] = set()

        self._worker = threading.Thread(
            target=self._loop, name="pipeline", daemon=True
        )
        self._worker.start()
        LOG.info("流水线已启动")

    # ---------------- 对外接口 ----------------

    def submit(self, task_id: int) -> int:
        """把任务丢进队列，返回前面还排着几个."""
        self._q.put(task_id)
        return max(0, self._q.qsize() - 1)

    def cancel(self, task_id: int) -> str:
        """取消任务。正在跑的会打断 ComfyUI，排队中的直接作废."""
        with self._lock:
            self._cancel_ids.add(task_id)
            running = self._current_id == task_id
            pid = self._current_pid

        if running:
            self.comfy.interrupt()
            LOG.info("已打断正在生成的任务 #%s", task_id)
            return "已打断当前生成"

        row = self.store.get(task_id)
        if row and row.get("status") == STATUS_QUEUED:
            self.store.update(
                task_id, status=STATUS_CANCELED, error="已取消", finished_at=time.time()
            )
            if pid:
                self.comfy.cancel_pending(pid)
            return "已取消排队任务"
        return "该任务已结束，无需取消"

    def queue_info(self) -> dict[str, Any]:
        with self._lock:
            cur = self._current_id
        return {"current": cur, "waiting": self._q.qsize()}

    # ---------------- 项目专库路径 ----------------

    def output_dirs(self, project: str) -> tuple[Path, Path, Path]:
        """返回某任务的 (raw_dir, upscaled_dir, thumbs_dir)，并确保都已创建。

        - 指定 project：落到 <outputs_dir>/<project>/{raw,upscaled,thumbs}
        - 未指定 project：沿用全局 results_dir / thumbs_dir（向后兼容旧任务）
        """
        if project:
            base = self.outputs_dir / project
            raw_dir = base / "raw"
            up_dir = base / "upscaled"
            th_dir = base / "thumbs"
        else:
            raw_dir = up_dir = self.results_dir
            th_dir = self.thumbs_dir
        for d in (raw_dir, up_dir, th_dir):
            d.mkdir(parents=True, exist_ok=True)
        return raw_dir, up_dir, th_dir

    @staticmethod
    def _safe_resolve(rel: str, *bases: Path) -> Path | None:
        """把相对路径解析到某个基目录内，越界/非法一律返回 None。

        防穿越：禁止 ``..``、绝对路径、冒号（Windows 盘符），且解析后必须仍落在
        某个 base 之下。只用于服务端校验，不限制扩展名（由调用方决定）。
        """
        if not rel or ".." in rel or rel.startswith("/") or ":" in rel:
            return None
        for b in bases:
            try:
                cand = (b / rel).resolve()
                cand.relative_to(b.resolve())
            except (ValueError, OSError):
                continue
            if cand.is_file():
                return cand
        return None

    def resolve_asset(self, rel: str) -> Path | None:
        """按相对路径定位一个已生成文件，依次在 outputs_dir / results_dir /
        thumbs_dir 中查找（新项目库优先，旧库回退）。找不到返回 None。"""
        return self._safe_resolve(
            rel, self.outputs_dir, self.results_dir, self.thumbs_dir
        )

    # ---------------- 后台循环 ----------------

    def _loop(self) -> None:
        while True:
            task_id = self._q.get()
            try:
                with self._lock:
                    if task_id in self._cancel_ids:
                        self._cancel_ids.discard(task_id)
                        self.store.update(
                            task_id,
                            status=STATUS_CANCELED,
                            error="已取消",
                            finished_at=time.time(),
                        )
                        continue
                    self._current_id = task_id
                    self._current_pid = ""
                self._run_one(task_id)
            except Exception as exc:  # 兜底：任何异常都不许弄死 worker
                LOG.exception("任务 #%s 执行异常", task_id)
                self.store.update(
                    task_id,
                    status=STATUS_FAILED,
                    error=str(exc)[:2000],
                    finished_at=time.time(),
                )
            finally:
                with self._lock:
                    self._current_id = None
                    self._current_pid = ""
                    self._cancel_ids.discard(task_id)
                self._q.task_done()

    def _canceled(self, task_id: int) -> bool:
        with self._lock:
            return task_id in self._cancel_ids

    def _run_one(self, task_id: int) -> None:
        row = self.store.get(task_id)
        if not row:
            LOG.warning("任务 #%s 不存在，跳过", task_id)
            return

        started = time.time()
        self.store.update(
            task_id,
            status=STATUS_RUNNING,
            started_at=started,
            stage_text="正在生成视频",
            error="",
        )
        LOG.info(
            "=== 任务 #%s 开始：%s / %s / %s秒 / %s ===",
            task_id,
            row.get("mode"),
            row.get("workflow"),
            row.get("duration"),
            row.get("resolution"),
        )

        # 看门狗：单个任务整体执行超时保险，避免永久卡死拖垮整条串行队列
        box: dict[str, object] = {}

        def _core() -> None:
            try:
                self._work_core(task_id, row, started)
            except Exception as exc:  # noqa: BLE001
                box["err"] = exc

        th = threading.Thread(target=_core, daemon=True)
        th.start()
        hard = int(self.comfy.timeout or 900) + 600
        th.join(hard)
        if th.is_alive():
            LOG.error("任务 #%s 整体执行超过 %ss，强制中断并标记失败", task_id, hard)
            try:
                self.comfy.interrupt()
                import requests as _rq

                self.comfy._session = _rq.Session()  # 重置可能残留的卡死连接
            except Exception:  # noqa: BLE001
                pass
            self.store.update(
                task_id,
                status=STATUS_FAILED,
                error=f"执行整体超时（{hard}秒），已强制中断 ComfyUI",
                finished_at=time.time(),
                stage_text="",
            )
            return  # run 循环 finally 会清理并继续队列

        if "err" in box:
            raise box["err"]  # type: ignore[misc]  # 交给 run 循环兜底标记 failed

    def _work_core(self, task_id: int, row: dict, started: float) -> None:
        # ---- 1. 图片上传到 ComfyUI ----
        first_ref = last_ref = ""
        if row.get("first_image"):
            first_ref = self.comfy.upload_image(self.uploads_dir / row["first_image"])
        if row.get("last_image"):
            last_ref = self.comfy.upload_image(self.uploads_dir / row["last_image"])

        # 万能参考：多张参考图上传（支持项目子目录相对路径）
        refs: list[str] = []
        raw_refs = (row.get("refs") or "").split("|")
        for r in raw_refs:
            r = r.strip()
            if not r:
                continue
            try:
                refs.append(self.comfy.upload_image(self.uploads_dir / r))
            except ComfyError as exc:
                LOG.warning("参考图 %s 上传失败，跳过: %s", r, exc)

        # 项目专库：决定成品 / 原片 / 封面落在哪个目录
        project = (row.get("project") or "").strip()
        raw_dir, up_dir, th_dir = self.output_dirs(project)

        # ---- 2. 生成 ----
        def on_submit(pid: str) -> None:
            with self._lock:
                self._current_pid = pid
            self.store.update(task_id, prompt_id=pid)

        def on_tick(elapsed: float) -> None:
            state, ahead = self.comfy.queue_state(self._current_pid)
            if state == "pending":
                text = f"ComfyUI 排队中（前面 {ahead} 个）"
            else:
                text = f"正在生成视频 {int(elapsed)} 秒"
            self.store.update(task_id, stage_text=text)

        try:
            raw = self.comfy.generate(
                prompt=row["prompt"],
                negative=row.get("negative") or "",
                seed=row.get("seed") or None,
                ratio=row.get("ratio") or None,
                duration=row.get("duration") or None,
                resolution=row.get("resolution") or None,
                workflow_name=row.get("workflow") or None,
                steps=row.get("steps") or None,
                first_image=first_ref,
                last_image=last_ref,
                ref_images=refs,
                on_submit=on_submit,
                on_tick=on_tick,
            )
        except ComfyError as exc:
            if self._canceled(task_id):
                self.store.update(
                    task_id,
                    status=STATUS_CANCELED,
                    error="已取消",
                    finished_at=time.time(),
                    stage_text="",
                )
                LOG.info("任务 #%s 已取消", task_id)
                return
            raise

        if self._canceled(task_id):
            self.store.update(
                task_id,
                status=STATUS_CANCELED,
                error="已取消",
                finished_at=time.time(),
                stage_text="",
            )
            return

        self.store.update(task_id, raw_video=str(raw))
        LOG.info("任务 #%s 原片: %s", task_id, raw)

        # 原片留档
        if self.keep_source_dir:
            try:
                self.keep_source_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(raw, self.keep_source_dir / raw.name)
            except OSError as exc:
                LOG.warning("原片留档失败（不影响主流程）: %s", exc)

        # 项目专库：超分前的原片也归档一份到 <project>/raw/
        if project:
            try:
                shutil.copy2(raw, raw_dir / raw.name)
            except OSError as exc:
                LOG.warning("项目原片归档失败（不影响主流程）: %s", exc)

        # ---- 3. 交付成品（视频超分 / 文生图直接交付） ----
        if raw.suffix.lower() in IMAGE_EXTS:
            final_path, final_name, thumb_name = self._finish_image(
                task_id, raw, row, up_dir, th_dir, project
            )
        else:
            final_path, final_name, thumb_name = self._finish_video(
                task_id, raw, row, started, up_dir, th_dir, project
            )

        # ---- 5. 网盘备份 ----
        netdisk_path = ""
        if self.delivery:
            self.store.update(
                task_id, status=STATUS_DELIVERING, stage_text="正在复制到百度网盘"
            )
            try:
                name = self.delivery.build_filename(row["prompt"], str(task_id))
                dest = self.delivery.deliver(final_path, name)
                netdisk_path = self.delivery.netdisk_path(dest)
                LOG.info("任务 #%s 已投递网盘: %s", task_id, netdisk_path)
            except Exception as exc:  # 网盘失败不影响成品
                LOG.warning("任务 #%s 网盘投递失败: %s", task_id, exc)
                netdisk_path = f"投递失败: {exc}"[:300]

        elapsed = time.time() - started
        self.store.update(
            task_id,
            status=STATUS_DONE,
            finished_at=time.time(),
            elapsed=elapsed,
            netdisk_path=netdisk_path,
            stage_text="",
        )
        LOG.info("=== 任务 #%s 完成，耗时 %.0f 秒 -> %s ===", task_id, elapsed, final_name)

    # ---------------- 交付：视频 ----------------

    def _finish_video(self, task_id, raw, row, started, up_dir, th_dir, project=""):
        """视频：超分到 1080P（可跳过），抽帧做封面。

        成品落到 up_dir，封面落到 th_dir；历史（无 project）任务仍用全局
        results_dir / thumbs_dir，result_file / thumb_file 记录相对路径以便回放。
        """
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(started))
        bare = f"{task_id:05d}_{stamp}_{safe_slug(row['prompt'])}.mp4"
        final_path = up_dir / bare

        want_upscale = bool(row.get("upscale")) and self.topaz.enabled
        if want_upscale:
            self.store.update(
                task_id, status=STATUS_UPSCALING, stage_text="正在超分到 1080P"
            )
            try:
                self.topaz.upscale(raw, final_path)
            except TopazError as exc:
                LOG.error("任务 #%s 超分失败，改用原片交付: %s", task_id, exc)
                shutil.copy2(raw, final_path)
                self.store.update(task_id, error=f"超分失败已用原片: {exc}"[:2000])
        else:
            shutil.copy2(raw, final_path)

        result_rel = f"{project}/upscaled/{bare}" if project else bare
        self.store.update(task_id, result_file=result_rel)

        thumb_bare = f"{task_id:05d}.jpg"
        thumb_path = th_dir / thumb_bare
        if self._make_thumb(final_path, thumb_path):
            thumb_rel = f"{project}/thumbs/{thumb_bare}" if project else thumb_bare
            self.store.update(task_id, thumb_file=thumb_rel)
        return final_path, result_rel, (thumb_rel if project else thumb_bare)

    # ---------------- 交付：图片（文生图） ----------------

    def _finish_image(self, task_id, raw, row, up_dir, th_dir, project=""):
        """文生图：不超分，图片本身即成品与封面。"""
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(time.time()))
        ext = raw.suffix.lower().lstrip(".") or "png"
        bare = f"{task_id:05d}_{stamp}_{safe_slug(row['prompt'])}.{ext}"
        final_path = up_dir / bare
        shutil.copy2(raw, final_path)
        # 图片本身即封面：thumb 直接复用成品文件（前端按 /image/<result_file> 展示）
        result_rel = f"{project}/upscaled/{bare}" if project else bare
        self.store.update(task_id, result_file=result_rel, thumb_file=result_rel)
        return final_path, result_rel, result_rel

    # ---------------- 工具 ----------------

    def _make_thumb(self, video: Path, dst: Path) -> bool:
        """抽第一帧当封面。没有 ffmpeg 就算了，不影响主流程."""
        ffmpeg = self._ffmpeg
        if not ffmpeg or not Path(ffmpeg).exists():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video), "-frames:v", "1", "-q:v", "4",
            "-vf", "scale=480:-2", str(dst),
        ]
        try:
            subprocess.run(
                cmd, timeout=120, capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            LOG.debug("抽封面失败: %s", exc)
            return False
        return dst.exists() and dst.stat().st_size > 1000

    def health(self) -> dict[str, Any]:
        """自检：ComfyUI / Topaz / 网盘。

        各项独立探测、带硬超时；ComfyUI 探测不复用 worker 可能占满的共享
        Session，避免单个卡死任务把健康检查也拖垮（前端一直「检测中」）。
        """
        import concurrent.futures as _cf
        import requests as _requests

        def probe_comfy() -> dict[str, Any]:
            st = _requests.get(
                f"{self.comfy.base}/system_stats", timeout=5
            ).json()
            dev = (st.get("devices") or [{}])[0]
            vram_free = dev.get("vram_free") or 0
            return {
                "ok": True,
                "version": (st.get("system") or {}).get("comfyui_version", "?"),
                "gpu": dev.get("name", "?"),
                "vram_free_gb": round(vram_free / 1024**3, 1),
            }

        def probe_topaz() -> dict[str, Any]:
            return {"ok": True, **self.topaz.check()}

        def probe_netdisk() -> dict[str, Any]:
            if self.delivery:
                return {"ok": True, **self.delivery.check()}
            return {"ok": True, "enabled": False}

        result: dict[str, Any] = {}
        with _cf.ThreadPoolExecutor(max_workers=3) as ex:
            futs = {
                "comfyui": ex.submit(probe_comfy),
                "topaz": ex.submit(probe_topaz),
                "netdisk": ex.submit(probe_netdisk),
            }
            for key, fut in futs.items():
                try:
                    result[key] = fut.result(timeout=6)
                except Exception as exc:  # 含超时
                    result[key] = {"ok": False, "error": f"探测异常: {exc}"}
        return result
