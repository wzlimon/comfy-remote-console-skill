"""ComfyUI HTTP API 客户端.

流程：注入提示词 -> POST /prompt -> 轮询 /history -> 定位产出视频。
"""

from __future__ import annotations

import copy
import json
import logging
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from .config import Config

LOG = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".gif"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

# 工作流里常见的提示词字段名，用于自动探测
PROMPT_KEYS = (
    "prompt",
    "text",
    "positive",
    "positive_prompt",
    "text_positive",
    "string",
)


class ComfyError(Exception):
    """ComfyUI 执行失败."""


class ComfyClient:
    """ComfyUI 客户端."""

    def __init__(self, cfg: Config) -> None:
        host = cfg.get("comfyui.host", "127.0.0.1")
        port = int(cfg.get("comfyui.port", 8188))
        self.base = f"http://{host}:{port}"

        self.output_dir = Path(str(cfg.get("comfyui.output_dir", "")))
        self.timeout = int(cfg.get("comfyui.timeout", 900))
        self.poll_interval = float(cfg.get("comfyui.poll_interval", 3))

        # 工作流选择：表单「流程」单选标签 -> 工作流名
        self.workflow_options: dict[str, str] = cfg.get("comfyui.workflow_options", {}) or {}
        self.workflow_default = str(cfg.get("comfyui.workflow_default", "standard") or "standard")

        # 解析各工作流配置（file + inject）。结构：
        #   comfyui.workflows.<name>.file
        #   comfyui.workflows.<name>.inject.{prompt_node,ratio_node,...,steps_node,steps_default,steps_options}
        wf_cfgs = cfg.get("comfyui.workflows", {}) or {}
        self._workflows: dict[str, dict] = {}
        for name, wfc in wf_cfgs.items():
            if not isinstance(wfc, dict):
                continue
            self._workflows[name] = {
                "path": cfg.path(
                    f"comfyui.workflows.{name}.file", f"workflows/{name}.json"
                ),
                "inject": wfc.get("inject", {}) or {},
                "media_type": str(wfc.get("media_type", "video") or "video").strip()
                or "video",
            }
        if not self._workflows:
            # 兼容老配置：只有单个 comfyui.workflow + comfyui.inject
            single = cfg.get("comfyui.workflow")
            if single:
                self._workflows[self.workflow_default] = {
                    "path": cfg.path("comfyui.workflow", "workflows/t2v.json"),
                    "inject": cfg.get("comfyui.inject", {}) or {},
                }

        # 图生视频 / 首尾帧时，动态插入的缩放节点用的算法
        self.image_upscale_method = str(
            cfg.get("comfyui.image_upscale_method", "nearest-exact")
            or "nearest-exact"
        )

        self.client_id = str(uuid.uuid4())
        self._session = requests.Session()

        # 默认工作流（让 prompt_node 等属性始终有值；不显式选也能运行）
        self.set_workflow(self.workflow_default)

    # ---------------- 工作流切换 ----------------

    def set_workflow(self, name: str) -> None:
        """切换到指定工作流，把它的 inject 配置加载到本实例属性。"""
        if name not in self._workflows:
            avail = "、".join(self._workflows) or "(无)"
            raise ComfyError(f"未知工作流 {name!r}，已配置：{avail}")
        wf = self._workflows[name]
        self.workflow_path = wf["path"]
        inj = wf["inject"]
        # 产物类型：video（默认，走视频超分流程）或 image（文生图，直接交付）
        self.media_type = str(wf.get("media_type", "video") or "video").strip() or "video"

        self.prompt_node = str(inj.get("prompt_node", "") or "").strip()
        self.prompt_field = str(inj.get("prompt_field", "prompt") or "prompt")
        self.negative_node = str(inj.get("negative_node", "") or "").strip()
        self.negative_field = str(
            inj.get("negative_field", "negative_prompt") or "negative_prompt"
        )
        self.seed_node = str(inj.get("seed_node", "") or "").strip()
        self.seed_field = str(inj.get("seed_field", "seed") or "seed")

        # 比例 / 时长（可选注入）
        self.ratio_node = str(inj.get("ratio_node", "") or "").strip()
        self.ratio_field = str(inj.get("ratio_field", "aspect_ratio") or "aspect_ratio")
        self.ratio_options = inj.get("ratio_options") or {}
        self.ratio_default = str(inj.get("ratio_default", "") or "").strip()
        self.duration_node = str(inj.get("duration_node", "") or "").strip()
        self.duration_field = str(inj.get("duration_field", "value") or "value")
        self.duration_default = inj.get("duration_default", None)

        # 分辨率 / 质量（megapixels）
        self.resolution_node = str(inj.get("resolution_node", "") or "").strip()
        self.resolution_field = str(
            inj.get("resolution_field", "megapixels") or "megapixels"
        )
        self.resolution_options = inj.get("resolution_options") or {}
        try:
            self.resolution_default = float(inj.get("resolution_default", 0.4))
        except (TypeError, ValueError):
            self.resolution_default = 0.4

        # 文生图类尺寸：节点用 width/height 数值（如 EmptySD3LatentImage），
        # 按比例算出像素后注入；size_baseline 是长边像素。
        self.width_node = str(inj.get("width_node", "") or "").strip()
        self.width_field = str(inj.get("width_field", "width") or "width")
        self.height_node = str(inj.get("height_node", "") or "").strip()
        self.height_field = str(inj.get("height_field", "height") or "height")
        try:
            self.size_baseline = int(inj.get("size_baseline", 1280))
        except (TypeError, ValueError):
            self.size_baseline = 1280

        # 步数（steps）：仅部分工作流支持调整（如 Turbo 流程 4~8 步）
        self.steps_node = str(inj.get("steps_node", "") or "").strip()
        self.steps_field = str(inj.get("steps_field", "steps") or "steps")
        try:
            self.steps_default = int(inj.get("steps_default", 20))
        except (TypeError, ValueError):
            self.steps_default = 20
        self.steps_options = inj.get("steps_options") or []

        self._current_workflow = name
        LOG.info("已切换工作流：%s (%s)", name, self.workflow_path)

    # ---------------- 基础 ----------------

    def ping(self) -> dict[str, Any]:
        """检查服务是否在线，顺带返回显卡信息."""
        resp = self._session.get(f"{self.base}/system_stats", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def load_workflow(self) -> dict[str, Any]:
        """读取 API 格式工作流."""
        if not self.workflow_path.exists():
            raise ComfyError(
                f"找不到工作流文件: {self.workflow_path}\n"
                "  >> 在 ComfyUI 里打开你的文生视频工作流，"
                "用「工作流 -> 导出(API)」或 Save (API Format) 导出，放到 workflows/ 下"
            )
        with self.workflow_path.open("r", encoding="utf-8") as fh:
            wf = json.load(fh)

        if not isinstance(wf, dict) or not wf:
            raise ComfyError("工作流 JSON 格式不对，应该是 {节点ID: {...}} 的字典")

        first = next(iter(wf.values()))
        if not isinstance(first, dict) or "class_type" not in first:
            raise ComfyError(
                "这份 JSON 不是 API 格式（节点里没有 class_type）。\n"
                "  >> 必须用「导出(API)」/ Save (API Format)，"
                "普通的保存/导出格式不能直接提交"
            )
        return wf

    def inspect_workflow(self) -> list[dict[str, Any]]:
        """列出工作流里所有含文本输入的节点，方便定位提示词注入点."""
        wf = self.load_workflow()
        found: list[dict[str, Any]] = []
        for node_id, node in wf.items():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs", {}) or {}
            text_inputs = {
                k: v
                for k, v in inputs.items()
                if isinstance(v, str) and len(v) > 0
            }
            other_scalars = {
                k: v for k, v in inputs.items() if isinstance(v, (int, float, bool))
            }
            if text_inputs or other_scalars:
                found.append(
                    {
                        "node_id": node_id,
                        "class_type": node.get("class_type", "?"),
                        "title": (node.get("_meta") or {}).get("title", ""),
                        "text_inputs": text_inputs,
                        "scalar_inputs": other_scalars,
                        "likely_prompt": any(
                            k in PROMPT_KEYS for k in text_inputs
                        ),
                    }
                )
        return found

    # ---------------- 图片上传 / 队列控制 ----------------

    def upload_image(self, local_path: Path, subfolder: str = "mobile") -> str:
        """把本地图片传到 ComfyUI 的 input 目录，返回 LoadImage 能用的文件名。

        返回值形如 'mobile/xxx.png'（带子目录），LoadImage 的 image 字段直接吃这个。
        """
        local_path = Path(local_path)
        if not local_path.exists():
            raise ComfyError(f"要上传的图片不存在: {local_path}")

        with local_path.open("rb") as fh:
            files = {"image": (local_path.name, fh, "application/octet-stream")}
            data = {"overwrite": "true", "type": "input"}
            if subfolder:
                data["subfolder"] = subfolder
            resp = self._session.post(
                f"{self.base}/upload/image", files=files, data=data, timeout=120
            )

        if resp.status_code != 200:
            raise ComfyError(
                f"上传图片到 ComfyUI 失败 HTTP {resp.status_code}: {resp.text[:500]}"
            )

        info = resp.json() or {}
        name = info.get("name") or local_path.name
        sub = info.get("subfolder") or ""
        ref = f"{sub}/{name}" if sub else name
        LOG.info("图片已上传 ComfyUI: %s", ref)
        return ref

    def interrupt(self) -> None:
        """打断 ComfyUI 当前正在执行的任务."""
        try:
            self._session.post(f"{self.base}/interrupt", timeout=15)
            LOG.info("已向 ComfyUI 发送打断指令")
        except requests.RequestException as exc:
            LOG.warning("打断失败: %s", exc)

    def cancel_pending(self, prompt_id: str) -> None:
        """把还在排队（未开始）的任务从队列里删掉."""
        try:
            self._session.post(
                f"{self.base}/queue", json={"delete": [prompt_id]}, timeout=15
            )
            LOG.info("已从队列移除 prompt_id=%s", prompt_id)
        except requests.RequestException as exc:
            LOG.warning("移除队列任务失败: %s", exc)

    def queue_state(self, prompt_id: str) -> tuple[str, int]:
        """查任务在 ComfyUI 队列里的状态。

        返回 (状态, 前面还排着几个)：状态取值 running / pending / unknown。
        """
        try:
            # 独立请求：本方法会被 Web 线程并发调用，不复用 self._session
            resp = requests.get(f"{self.base}/queue", timeout=15)
            resp.raise_for_status()
            q = resp.json() or {}
        except (requests.RequestException, ValueError):
            return "unknown", 0

        for item in q.get("queue_running") or []:
            if len(item) > 1 and item[1] == prompt_id:
                return "running", 0

        pending = q.get("queue_pending") or []
        for idx, item in enumerate(pending):
            if len(item) > 1 and item[1] == prompt_id:
                return "pending", idx
        return "unknown", 0

    # ---------------- 注入 ----------------

    @staticmethod
    def _normalize_ratio(raw: str) -> str:
        """把各种写法归一化，提取核心比例 W:H（如 '9:16'）。

        兼容：全角冒号、空格、中文方向词（竖屏/横屏/方形/标清…）。
        """
        s = (raw or "").strip()
        s = s.replace("：", ":")  # 全角 -> 半角
        s = s.replace("＊", "*").replace("x", ":").replace("X", ":")
        for junk in [
            "竖屏", "横屏", "方形", "超宽屏", "宽屏",
            "标清", "高清", "标准", "屏", " ", "　",
            "（", "）", "(", ")",
        ]:
            s = s.replace(junk, "")
        m = re.search(r"(\d+)\s*[:：]\s*(\d+)", s)
        if m:
            return f"{int(m.group(1))}:{int(m.group(2))}"
        return ""

    def _resolve_ratio(self, raw: str) -> str:
        """把表单值解析成 ResolutionSelector 接受的 aspect_ratio 字符串。"""
        if not raw:
            return ""
        raw = raw.strip()
        # 1) 精确匹配友好名（config 里 ratio_options 的键）
        if raw in self.ratio_options:
            return self.ratio_options[raw]
        # 2) 已经是合法值（如 '9:16 (Portrait Widescreen)'）
        if raw in set(self.ratio_options.values()):
            return raw
        # 3) 归一化后按核心 W:H 匹配
        core = self._normalize_ratio(raw)
        if core:
            for val in self.ratio_options.values():
                if val.startswith(core + " ") or val.startswith(core + "("):
                    return val
        return ""

    def _resolve_resolution(self, raw: str) -> float | None:
        """把表单值解析成 megapixels 数值（如 0.4 / 2.0）。

        支持：友好名（config 里 resolution_options 的键，如 '480P'）、
        直接数字（'0.4' / '2' / '1.5'），以及带单位的写法（'1080P' -> 2.0）。
        范围限制在 0.1~4.0 之间（超出视为非法，回退默认）。
        """
        if not raw:
            return None
        raw = str(raw).strip()
        # 1) 友好名精确匹配
        if raw in self.resolution_options:
            try:
                return float(self.resolution_options[raw])
            except (TypeError, ValueError):
                return None
        # 2) 直接数字或带单位的数字（提取第一个浮点数）
        m = re.search(r"(\d+(?:\.\d+)?)", raw)
        if m:
            try:
                f = float(m.group(1))
                if 0.1 <= f <= 4.0:
                    return f
            except ValueError:
                pass
        return None

    def _resolve_pixel_size(self, raw: str):
        """把比例（友好名或 'W:H'）解析成 (width, height) 像素，长边 = size_baseline。

        用于文生图类工作流（节点直接用 width/height 数值，而非 aspect_ratio 字符串）。
        """
        core = self._normalize_ratio(raw)
        if not core:
            return None
        try:
            w0, h0 = (int(x) for x in core.split(":"))
        except ValueError:
            return None
        if w0 <= 0 or h0 <= 0:
            return None
        base = self.size_baseline
        if w0 >= h0:
            width = base
            height = max(1, round(base * h0 / w0))
        else:
            height = base
            width = max(1, round(base * w0 / h0))
        return width, height

    def build_prompt(
        self,
        prompt: str,
        negative: str = "",
        seed: int | None = None,
        ratio: str | None = None,
        duration: int | None = None,
        resolution: str | None = None,
        steps: int | None = None,
        first_image: str = "",
        last_image: str = "",
        ref_images: list[str] | None = None,
    ) -> dict[str, Any]:
        """把提示词/比例/时长/分辨率/种子注入工作流副本.

        first_image / last_image 是已上传到 ComfyUI input 目录的图片引用名
        （由 upload_image 返回）。给了 first_image 就是图生视频，
        再给 last_image 就是首尾帧控制。
        ref_images 是万能参考生视频用的多张参考图（最多 3 张）。
        """
        wf = copy.deepcopy(self.load_workflow())
        mp_target = self.resolution_default

        if not self.prompt_node:
            raise ComfyError(
                "config.yaml 里 comfyui.inject.prompt_node 没填。\n"
                "  >> 运行 python doctor.py --inspect-workflow 查看候选节点"
            )
        if self.prompt_node not in wf:
            raise ComfyError(
                f"工作流里没有节点 {self.prompt_node}，"
                f"现有节点: {', '.join(sorted(wf.keys())[:20])}"
            )

        node = wf[self.prompt_node]
        node.setdefault("inputs", {})
        if self.prompt_field not in node["inputs"]:
            LOG.warning(
                "节点 %s 原本没有字段 %s，将新增（可能不生效，请核对）",
                self.prompt_node,
                self.prompt_field,
            )
        node["inputs"][self.prompt_field] = prompt
        LOG.info("提示词已注入节点 %s.%s", self.prompt_node, self.prompt_field)

        if negative and self.negative_node and self.negative_node in wf:
            wf[self.negative_node].setdefault("inputs", {})
            wf[self.negative_node]["inputs"][self.negative_field] = negative
            LOG.info("反向提示词已注入节点 %s", self.negative_node)

        # 种子：显式指定就用指定值，否则随机，避免相同提示词出一样的片子
        if self.seed_node and self.seed_node in wf:
            actual = seed if seed else random.randint(1, 2**31 - 1)
            wf[self.seed_node].setdefault("inputs", {})
            wf[self.seed_node]["inputs"][self.seed_field] = actual
            LOG.info("种子 %s 已注入节点 %s", actual, self.seed_node)

        # 尺寸注入：两种模式
        #  A) 文生图类：直接算出像素宽高注入 width/height 字段
        #     （节点是 EmptySD3LatentImage 之类，没有 aspect_ratio 概念）
        #  B) 视频类：把友好名映射成 aspect_ratio 字符串注入 ResolutionSelector
        if self.width_node and self.height_node and self.width_node in wf and self.height_node in wf:
            raw = (ratio or "").strip() or self.ratio_default
            wh = self._resolve_pixel_size(raw)
            if wh:
                wf[self.width_node].setdefault("inputs", {})[self.width_field] = wh[0]
                wf[self.height_node].setdefault("inputs", {})[self.height_field] = wh[1]
                LOG.info(
                    "尺寸 %sx%s 已注入 %s.%s / %s.%s (比例=%s)",
                    wh[0], wh[1], self.width_node, self.width_field,
                    self.height_node, self.height_field, raw,
                )
            else:
                LOG.warning("比例「%s」无法解析为像素尺寸，沿用工作流默认", raw)
        elif self.ratio_node and self.ratio_node in wf:
            raw = (ratio or "").strip() or self.ratio_default
            actual = self._resolve_ratio(raw)
            if actual:
                wf[self.ratio_node].setdefault("inputs", {})[
                    self.ratio_field
                ] = actual
                LOG.info(
                    "比例已注入节点 %s.%s = %s (表单值=%r)",
                    self.ratio_node,
                    self.ratio_field,
                    actual,
                    raw,
                )
            else:
                # 解析不出：回退默认，避免「没注入却以为成功了」
                if self.ratio_default:
                    wf[self.ratio_node].setdefault("inputs", {})[
                        self.ratio_field
                    ] = self.ratio_default
                    LOG.warning(
                        "比例「%s」无法识别，已用默认 %s", raw, self.ratio_default
                    )

        # 时长（秒）：注入到 PrimitiveFloat 节点的 value 字段
        if self.duration_node and self.duration_node in wf:
            dur = duration if duration else self.duration_default
            if dur:
                try:
                    dur_int = int(dur)
                    wf[self.duration_node].setdefault("inputs", {})[
                        self.duration_field
                    ] = dur_int
                    LOG.info("时长 %s 秒已注入节点 %s", dur_int, self.duration_node)
                except (TypeError, ValueError):
                    LOG.warning("时长值无效，跳过: %r", dur)

        # 分辨率 / 质量（megapixels）：注入到 ResolutionSelector(115)
        #   0.4≈480P，2.0≈1080P。该节点会用它（结合 aspect_ratio）算出具体宽高，
        #   沿用其已验证的算法，不自行算宽高。
        if self.resolution_node and self.resolution_node in wf:
            raw = (resolution or "").strip()
            val = self._resolve_resolution(raw)
            target = val if val is not None else self.resolution_default
            mp_target = target
            wf[self.resolution_node].setdefault("inputs", {})[
                self.resolution_field
            ] = target
            if val is None and raw:
                LOG.warning(
                    "分辨率「%s」无法识别，已用默认 %s", raw, self.resolution_default
                )
            else:
                LOG.info(
                    "分辨率已注入节点 %s.%s = %s (表单值=%r)",
                    self.resolution_node,
                    self.resolution_field,
                    target,
                    raw,
                )

        # 步数（steps）：注入到采样器节点的 steps 字段（如 BasicScheduler）。
        # 仅当该工作流配置了 steps_node 才注入；按 steps_options 限幅，缺省用 steps_default。
        if self.steps_node and self.steps_node in wf:
            val = steps if steps is not None else self.steps_default
            if self.steps_options and val not in self.steps_options:
                LOG.warning(
                    "步数 %s 不在可选范围 %s，回退默认 %s",
                    val, self.steps_options, self.steps_default,
                )
                val = self.steps_default
            wf[self.steps_node].setdefault("inputs", {})[self.steps_field] = int(val)
            LOG.info("步数 %s 已注入节点 %s.%s", int(val), self.steps_node, self.steps_field)

        # 图生视频 / 首尾帧：动态插入 LoadImage 链，并让视频宽高跟随图片比例
        if first_image:
            self._attach_images(wf, first_image, last_image, mp_target)

        # 万能参考生视频：注入多张参考图
        if ref_images:
            self._attach_references(wf, ref_images)

        return wf

    # 动态插入的节点 ID，用大号数字避免和工作流里原有节点撞车
    NID_FIRST_LOAD = "9101"
    NID_FIRST_SCALE = "9102"
    NID_SIZE = "9103"
    NID_LAST_LOAD = "9104"
    NID_LAST_SCALE = "9105"

    def _attach_images(
        self,
        wf: dict[str, Any],
        first_image: str,
        last_image: str,
        megapixels: float,
    ) -> None:
        """往工作流里接首帧（可选尾帧）图片。

        接法：
            LoadImage -> ImageScaleToTotalPixels -> MiniMax.first_frame
                                                 -> GetImageSize -> MiniMax.width/height
        视频宽高改为跟随图片，所以图生模式下不需要再选比例。
        尾帧会被缩放到与首帧完全相同的尺寸，否则模型会报尺寸不一致。
        """
        mm = self.prompt_node
        if not mm or mm not in wf:
            raise ComfyError(
                f"图生视频需要往节点 {mm!r} 接图片，但工作流里找不到这个节点"
            )

        node = wf[mm]
        node.setdefault("inputs", {})

        wf[self.NID_FIRST_LOAD] = {
            "class_type": "LoadImage",
            "inputs": {"image": first_image},
            "_meta": {"title": "首帧（手机上传）"},
        }
        wf[self.NID_FIRST_SCALE] = {
            "class_type": "ImageScaleToTotalPixels",
            "inputs": {
                "image": [self.NID_FIRST_LOAD, 0],
                "upscale_method": self.image_upscale_method,
                "megapixels": float(megapixels),
                "resolution_steps": 32,
            },
        }
        wf[self.NID_SIZE] = {
            "class_type": "GetImageSize",
            "inputs": {"image": [self.NID_FIRST_SCALE, 0]},
        }

        node["inputs"]["first_frame"] = [self.NID_FIRST_SCALE, 0]
        node["inputs"]["width"] = [self.NID_SIZE, 0]
        node["inputs"]["height"] = [self.NID_SIZE, 1]
        LOG.info("首帧已接入节点 %s，宽高改为跟随图片", mm)

        if last_image:
            wf[self.NID_LAST_LOAD] = {
                "class_type": "LoadImage",
                "inputs": {"image": last_image},
                "_meta": {"title": "尾帧（手机上传）"},
            }
            wf[self.NID_LAST_SCALE] = {
                "class_type": "ImageScale",
                "inputs": {
                    "image": [self.NID_LAST_LOAD, 0],
                    "upscale_method": "lanczos",
                    "width": [self.NID_SIZE, 0],
                    "height": [self.NID_SIZE, 1],
                    "crop": "center",
                },
            }
            node["inputs"]["last_frame"] = [self.NID_LAST_SCALE, 0]
            LOG.info("尾帧已接入节点 %s（已对齐首帧尺寸）", mm)

    # ---------------- 参考图注入（万能参考 r2v） ----------------

    @staticmethod
    def _new_node_id(wf: dict[str, Any], base: int) -> str:
        """找一个没被占用的大号节点 id（字符串）。"""
        nid = base
        while str(nid) in wf:
            nid += 1
        return str(nid)

    def _attach_references(
        self,
        wf: dict[str, Any],
        ref_images: list[str],
    ) -> None:
        """万能参考生视频：把多张参考图接到 MiniMaxH3ReferenceToVideo 的 ref_images。

        接法：LoadImage(ref) -> ref_images.ref_image_0/1/2
        工作流里原本的参考图 LoadImage（137/139）会被替换成用户上传的图；
        若用户给的图少于工作流预置的，多余的 ref 输入和对应 LoadImage 节点会被删掉，
        避免去加载不存在的旧文件。音频/视频参考（ref_audios / ref_videos）在此模式下不用，
        一并清掉，并删除 LoadAudio 节点。
        """
        mm = self.prompt_node
        if not mm or mm not in wf:
            raise ComfyError(
                f"万能参考需要往节点 {mm!r} 接参考图，但工作流里找不到这个节点"
            )
        node = wf[mm]
        node.setdefault("inputs", {})

        # 1) 去掉音频/视频参考输入（本模式只用图片参考）
        for key in list(node["inputs"]):
            if key.startswith(("ref_audios", "ref_video_audios", "ref_videos")):
                node["inputs"].pop(key, None)

        # 2) 收集当前已接到 ref_image_0/1/2 的 LoadImage 节点
        existing: list[str | None] = []
        for idx in range(3):
            cur = node["inputs"].get(f"ref_images.ref_image_{idx}")
            if (
                isinstance(cur, list)
                and len(cur) == 2
                and cur[0] in wf
                and wf[cur[0]].get("class_type") == "LoadImage"
            ):
                existing.append(cur[0])
            else:
                existing.append(None)

        used: set[str] = set()
        refs = [r for r in (ref_images or []) if r]
        for idx in range(3):
            target = f"ref_images.ref_image_{idx}"
            if idx < len(refs):
                ref = refs[idx]
                load_id = existing[idx]
                if load_id and load_id in wf:
                    wf[load_id].setdefault("inputs", {})["image"] = ref
                    used.add(load_id)
                else:
                    new_id = self._new_node_id(wf, 9300 + idx)
                    wf[new_id] = {
                        "class_type": "LoadImage",
                        "inputs": {"image": ref},
                        "_meta": {"title": f"参考图{idx + 1}"},
                    }
                    used.add(new_id)
                    node["inputs"][target] = [new_id, 0]
            else:
                # 用户没提供这张参考图：移除该输入与对应 LoadImage 节点
                node["inputs"].pop(target, None)
                load_id = existing[idx]
                if load_id and load_id in wf and load_id not in used:
                    wf.pop(load_id, None)

        # 3) 删除 LoadAudio 节点（音频参考不再使用）
        for nid, nd in list(wf.items()):
            if nd.get("class_type") == "LoadAudio":
                wf.pop(nid, None)

        LOG.info("万能参考已接入节点 %s：%s 张参考图", mm, len(used))

    # ---------------- 执行 ----------------

    def submit(self, workflow: dict[str, Any]) -> str:
        """提交任务，返回 prompt_id."""
        payload = {"prompt": workflow, "client_id": self.client_id}
        resp = self._session.post(f"{self.base}/prompt", json=payload, timeout=60)

        if resp.status_code != 200:
            detail = resp.text[:1500]
            try:
                err = resp.json()
                node_errors = err.get("node_errors") or {}
                if node_errors:
                    detail = json.dumps(node_errors, ensure_ascii=False, indent=2)
                elif err.get("error"):
                    detail = json.dumps(err["error"], ensure_ascii=False)
            except ValueError:
                pass
            raise ComfyError(f"提交失败 HTTP {resp.status_code}:\n{detail}")

        data = resp.json()
        pid = data.get("prompt_id")
        if not pid:
            raise ComfyError(f"提交成功但没拿到 prompt_id: {data}")
        LOG.info("任务已提交 prompt_id=%s", pid)
        return pid

    def wait(self, prompt_id: str, on_tick=None) -> dict[str, Any]:
        """轮询直到任务完成，返回 history 条目.

        on_tick(elapsed_seconds): 每轮回调一次，供外部刷新进度显示。
        """
        deadline = time.time() + self.timeout
        started = time.time()
        last_log = 0.0

        while time.time() < deadline:
            if on_tick:
                try:
                    on_tick(time.time() - started)
                except Exception:  # 进度回调不该影响主流程
                    LOG.debug("进度回调异常", exc_info=True)
            try:
                resp = self._session.get(
                    f"{self.base}/history/{prompt_id}", timeout=20
                )
                resp.raise_for_status()
                hist = resp.json() or {}
            except requests.RequestException as exc:
                LOG.warning("查询进度失败（稍后重试）: %s", exc)
                time.sleep(self.poll_interval)
                continue

            entry = hist.get(prompt_id)
            if entry:
                status = entry.get("status", {}) or {}
                if status.get("completed") or status.get("status_str") == "success":
                    LOG.info("任务完成 prompt_id=%s", prompt_id)
                    return entry
                if status.get("status_str") == "error":
                    raise ComfyError(
                        f"ComfyUI 执行报错:\n{self._extract_error(status)}"
                    )

            now = time.time()
            if now - last_log > 30:
                remain = int(deadline - now)
                LOG.info("等待生成中... 剩余超时 %s 秒", remain)
                last_log = now
            time.sleep(self.poll_interval)

        raise ComfyError(f"等待超时（{self.timeout}秒），任务可能还在队列里")

    @staticmethod
    def _extract_error(status: dict[str, Any]) -> str:
        """从 status.messages 里挖出可读的错误."""
        lines: list[str] = []
        for msg in status.get("messages", []) or []:
            if not isinstance(msg, (list, tuple)) or len(msg) < 2:
                continue
            kind, body = msg[0], msg[1]
            if kind in ("execution_error", "execution_interrupted") and isinstance(
                body, dict
            ):
                lines.append(
                    f"  节点 {body.get('node_id')} ({body.get('node_type')}): "
                    f"{body.get('exception_type')} - {body.get('exception_message')}"
                )
        return "\n".join(lines) if lines else json.dumps(status, ensure_ascii=False)[:800]

    def locate_video(self, entry: dict[str, Any], since: float) -> Path:
        """从 history 结果里定位产出的视频文件."""
        candidates: list[str] = []

        for node_id, out in (entry.get("outputs") or {}).items():
            if not isinstance(out, dict):
                continue
            for key, items in out.items():
                if not isinstance(items, list):
                    continue
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    fname = it.get("filename")
                    if not fname:
                        continue
                    ext = Path(str(fname)).suffix.lower()
                    if ext not in VIDEO_EXTS:
                        continue
                    sub = it.get("subfolder") or ""
                    rel = f"{sub}/{fname}" if sub else str(fname)
                    candidates.append(rel)
                    LOG.debug("history 产出: 节点%s.%s -> %s", node_id, key, rel)

        for rel in candidates:
            full = self.output_dir / rel
            if full.exists():
                LOG.info("定位到视频: %s", full)
                return full

        # 兜底：history 没给出可用路径时，扫输出目录里任务开始后新增的视频
        LOG.warning("history 未给出可用视频路径，回退到扫描输出目录")
        newest = self._newest_video(since)
        if newest:
            LOG.info("扫描定位到视频: %s", newest)
            return newest

        raise ComfyError(
            "任务完成但找不到输出视频。\n"
            f"  history 候选: {candidates or '（空）'}\n"
            f"  输出目录: {self.output_dir}\n"
            "  >> 检查工作流里保存视频的节点，以及 config.yaml 的 comfyui.output_dir"
        )

    def _newest_video(self, since: float) -> Path | None:
        """找输出目录中 since 之后新增的最新视频."""
        if not self.output_dir.exists():
            return None
        best: Path | None = None
        best_mtime = since - 1
        for p in self.output_dir.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
                continue
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if mt > best_mtime:
                best, best_mtime = p, mt
        return best

    def locate_image(self, entry: dict[str, Any], since: float) -> Path:
        """从 history 结果里定位产出的图片文件（文生图）."""
        candidates: list[str] = []
        for node_id, out in (entry.get("outputs") or {}).items():
            if not isinstance(out, dict):
                continue
            for key, items in out.items():
                if not isinstance(items, list):
                    continue
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    fname = it.get("filename")
                    if not fname:
                        continue
                    ext = Path(str(fname)).suffix.lower()
                    if ext not in IMAGE_EXTS:
                        continue
                    sub = it.get("subfolder") or ""
                    rel = f"{sub}/{fname}" if sub else str(fname)
                    candidates.append(rel)
                    LOG.debug("history 产出(图): 节点%s.%s -> %s", node_id, key, rel)

        for rel in candidates:
            full = self.output_dir / rel
            if full.exists():
                LOG.info("定位到图片: %s", full)
                return full

        LOG.warning("history 未给出可用图片路径，回退到扫描输出目录")
        newest = self._newest_image(since)
        if newest:
            LOG.info("扫描定位到图片: %s", newest)
            return newest

        raise ComfyError(
            "任务完成但找不到输出图片。\n"
            f"  history 候选: {candidates or '（空）'}\n"
            f"  输出目录: {self.output_dir}\n"
            "  >> 检查工作流里保存图片的节点，以及 config.yaml 的 comfyui.output_dir"
        )

    def _newest_image(self, since: float) -> Path | None:
        """找输出目录中 since 之后新增的最新图片."""
        if not self.output_dir.exists():
            return None
        best: Path | None = None
        best_mtime = since - 1
        for p in self.output_dir.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
                continue
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if mt > best_mtime:
                best, best_mtime = p, mt
        return best

    def generate(
        self,
        prompt: str,
        negative: str = "",
        seed: int | None = None,
        ratio: str | None = None,
        duration: int | None = None,
        resolution: str | None = None,
        workflow_name: str | None = None,
        steps: int | None = None,
        first_image: str = "",
        last_image: str = "",
        ref_images: list[str] | None = None,
        on_submit=None,
        on_tick=None,
    ) -> Path:
        """一步到位：提交并等待，返回视频路径.

        workflow_name: 指定用哪个工作流（如 'standard' / 'turbo' / 'r2v'），
                       不传则用默认工作流。steps: 采样步数（仅 turbo 等流程有效）。
        first_image / last_image: 已上传的图片引用名，走图生视频 / 首尾帧。
        ref_images: 万能参考生视频用的多张参考图引用名（最多 3 张）。
        on_submit(prompt_id): 提交成功后回调，便于外部记录任务号、支持取消。
        on_tick(elapsed): 等待期间每轮回调，便于外部刷新进度。
        """
        started = time.time()
        if workflow_name:
            self.set_workflow(workflow_name)
        wf = self.build_prompt(
            prompt, negative, seed, ratio, duration, resolution, steps,
            first_image, last_image, ref_images,
        )
        pid = self.submit(wf)
        if on_submit:
            on_submit(pid)
        entry = self.wait(pid, on_tick=on_tick)
        # 留 1 秒余量，避免文件系统时间精度导致漏判
        if self.media_type == "image":
            return self.locate_image(entry, started - 1)
        return self.locate_video(entry, started - 1)
