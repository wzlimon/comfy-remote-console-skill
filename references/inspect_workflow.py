"""列出 ComfyUI 工作流（API 格式）JSON 里的所有节点：ID / 类型 / 标题 / 输入字段。

新机器适配时用它查清「提示词 / 种子 / 步数 / 比例 / 时长」分别该注入到哪个节点，
避免靠猜填错 config.yaml 的 inject 段。

用法：
    python inspect_workflow.py workflows/t2vt2.json
    python inspect_workflow.py workflows/zimage.json --inputs 133 135   # 只看指定节点的输入字段
    python inspect_workflow.py workflows/*.json --type CLIPTextEncode   # 只列某类节点
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 常见的「需要注入参数」的节点类型，列出时高亮，方便快速定位
KEY_TYPES = {
    "CLIPTextEncode",           # 提示词（文生图）
    "MiniMaxH3ImageToVideo",    # 提示词（H3 文生/图生视频）
    "MiniMaxH3ReferenceToVideo",  # 提示词（H3 参考生视频）
    "RandomNoise",              # 随机种子
    "BasicScheduler",           # 采样步数
    "ResolutionSelector",       # 画面比例 / 分辨率
    "PrimitiveFloat",           # 时长（秒）
    "ComfyMathExpression",      # 时长 -> 帧数换算
    "EmptySD3LatentImage",      # 图片宽高
    "KSampler",                 # 种子 + 步数
    "LoadImage",                # 图生视频 / 参考图入口
}


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[错误] {path} 不是合法 JSON：{exc}")

    if not isinstance(data, dict):
        raise SystemExit(f"[错误] {path} 不是 API 格式（顶层应为对象）")

    # UI 格式的 workflow 顶层有 nodes/links，API 格式没有
    if "nodes" in data or "links" in data:
        raise SystemExit(
            f"[错误] {path} 看起来是「UI 格式」的工作流，不是 API 格式。\n"
            f"       请在 ComfyUI 里用 Save (API Format) 重新导出。"
        )
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="查看 ComfyUI 工作流 JSON 的节点结构")
    ap.add_argument("files", nargs="+", help="工作流 JSON 路径")
    ap.add_argument("--inputs", nargs="*", default=None,
                    metavar="NODE_ID", help="额外打印指定节点的输入字段")
    ap.add_argument("--type", default=None,
                    help="只列出指定 class_type 的节点（如 CLIPTextEncode）")
    args = ap.parse_args()

    for fp in args.files:
        path = Path(fp)
        if not path.is_file():
            print(f"[跳过] 找不到文件：{path}")
            continue

        data = load(path)
        print("=" * 72)
        print(f"{path}   节点数：{len(data)}")
        print("=" * 72)
        print(f"{'节点ID':>8}  {'类型(class_type)':<30} {'标题(_meta.title)'}")
        print("-" * 72)

        shown = 0
        for nid, node in data.items():
            if not isinstance(node, dict):
                continue
            ctype = node.get("class_type", "")
            title = (node.get("_meta") or {}).get("title") or ""
            if args.type and ctype != args.type:
                continue
            mark = "  <<<" if ctype in KEY_TYPES else ""
            print(f"{nid:>8}  {ctype:<30} {title[:32]}{mark}")
            shown += 1

        if args.type and shown == 0:
            print(f"  （没有 class_type 为 {args.type} 的节点）")

        if args.inputs:
            print("-" * 72)
            for nid in args.inputs:
                node = data.get(nid)
                if node is None:
                    print(f"[警告] 节点 {nid} 不存在")
                    continue
                ctype = node.get("class_type", "")
                inputs = node.get("inputs", {})
                print(f"节点 {nid}（{ctype}）的输入字段：")
                if not inputs:
                    print("    （该节点没有 inputs）")
                for k, v in inputs.items():
                    # 连线引用显示为 [来源节点, 输出槽位]
                    if isinstance(v, list):
                        shown_v = f"[连线] <- {v[0]}:{v[1]}"
                    else:
                        s = str(v)
                        shown_v = s if len(s) <= 40 else s[:40] + "..."
                    print(f"    {k:<24} = {shown_v}")
        print()

    print("提示：把上面带 <<< 的节点 ID 填进 config.yaml 对应工作流的 inject 段。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
