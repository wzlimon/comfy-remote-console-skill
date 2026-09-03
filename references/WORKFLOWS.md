# 工作流（Workflow）规范：命名、位置、节点映射与新机器适配

> 这份文档是**新机器部署时最容易被忽略、也最容易出错**的部分。
> 界面能不能生成对、提交的参数能不能传进 ComfyUI，全看这一节。
> 参照实现（可直接照抄改）：`core/comfy.py` + `workflows/*.json` + `config.yaml` 的 `inject` 段。

---

## 1. 工作流文件放哪

工作流 JSON 统一放在**项目根 `workflows/` 目录下**，在 `config.yaml` 里用**相对项目根的路径**引用：

```yaml
comfyui:
  workflows:
    turbo:
      file: "workflows/t2vt2.json"   # ← 相对项目根
```

- 目录：`workflows/`（与 `core/`、`web/`、`server.py` 同级）
- 格式：必须是 **ComfyUI 导出的「API 格式」JSON**
  结构形如 `{"<节点ID>": {"class_type": "...", "inputs": {...}, "_meta": {"title": "..."}}}`
- ⚠️ **不能用 UI 格式的 workflow JSON**（UI 格式带 `nodes`/`links` 数组，提交给 `/prompt` 会被拒）

---

## 2. 命名：三个名字要分清（极易混淆）

同一个工作流有**三个不同的名字**，各司其职：

| 名字类型 | 在哪定义 | 例子 | 作用 |
|---|---|---|---|
| **文件名** | 磁盘上的 JSON | `t2vt2.json` | 物理文件 |
| **配置名**（workflow key） | `config.yaml` 的 `workflows.<key>` | `turbo` | 后端索引、提交时 `workflow=` 参数的值 |
| **显示名** | `config.yaml` 的 `workflow_options` 的 **key** | `Turbo加速` | 网页芯片上显示的中文 |

映射关系在 `workflow_options` 里建立：

```yaml
comfyui:
  workflow_options:
    "标准流程": "standard"    # 显示名 -> 配置名
    "Turbo加速": "turbo"
    "万能参考": "r2v"
    "参考加速": "r2vt"
    "文生图": "zimage"
  workflow_default: "turbo"
```

### 本机（MiniMax H3 / Z-IMAGE）实测对照表

| 文件名 | 配置名 | 显示名 | 用途 | 产出 | 备注 |
|---|---|---|---|---|---|
| `t2v.json` | `standard` | 标准流程 | 文生/图生/首尾帧，H3 标准模型，固定 20 步 | 视频 | 步数不可调 |
| `t2vt2.json` | `turbo` | Turbo加速 | 同上 + MiniMax-H3 Turbo LoRA，4~12 步 | 视频 | **日常首选**，默认 |
| `r2v.json` | `r2v` | 万能参考 | `MiniMaxH3ReferenceToVideo`，最多 3 张参考图保持主体一致 | 视频 | 不支持自定义步数 |
| `r2vt.json` | `r2vt` | 参考加速 | r2v + Turbo LoRA，4~12 步 | 视频 | 节点结构与 r2v 一致，多一个 LoRA 节点 145 |
| `r2vt00.json` | （未启用） | — | r2vt 的备用/旧版（含 LoadAudio） | 视频 | 当前未在 `workflow_options` 注册 |
| `zimage.json` | `zimage` | 文生图 | Z-IMAGE-turbo，8 步出图 | **图片** | 需标 `media_type: image`，不超分 |

> `r2vt00.json` 说明：它**没有**被注册进 `workflow_options`，所以界面上看不到。留着是为了回退。
> 只要不在 `workflow_options` 里注册，文件存在也不会出现在界面上。

### 模式 → 可选流程（`mode_workflows`）

不是每个模式都能选所有流程，用 `mode_workflows` 限定：

```yaml
mode_workflows:
  t2v: ["standard", "turbo"]   # 文生视频
  i2v: ["standard", "turbo"]   # 图生视频
  flf: ["standard", "turbo"]   # 首尾帧
  r2v: ["r2v", "r2vt"]         # 万能参考（只有参考系列）
  t2i: []                      # 文生图（空=隐藏流程芯片，前端自动隐藏）
```

- 模式固定五种：`t2v`(文字生成) / `i2v`(图片生成) / `flf`(首尾帧) / `r2v`(万能参考) / `t2i`(文生图)
- 列表**留空** → 前端隐藏「流程」字段
- 列表里配置了但 `workflows` 里没定义的名字 → 服务端自动过滤掉（防指到不存在的工作流）

---

## 3. 核心：节点 ID 映射（`inject` 段）

**这是新机器最容易错的地方。** 网页上的「步数 / 比例 / 时长 / 分辨率」不是 ComfyUI 原生参数，
而是由后端**按节点 ID 注入**到工作流 JSON 对应节点的 `inputs` 字段里。

```yaml
comfyui:
  workflows:
    turbo:
      file: "workflows/t2vt2.json"
      inject:
        prompt_node: "133"              # 提示词注入到哪个节点
        prompt_field: "prompt"          # 该节点的哪个输入字段
        seed_node: "131"
        seed_field: "noise_seed"
        ratio_node: "115"
        ratio_field: "aspect_ratio"
        duration_node: "135"
        duration_field: "value"
        resolution_node: "115"
        resolution_field: "megapixels"
        steps_node: "126"
        steps_field: "steps"
```

### inject 字段全表

| 字段 | 作用 | 留空 / 缺省的含义 |
|---|---|---|
| `prompt_node` / `prompt_field` | 正向提示词 | 必填 |
| `negative_node` / `negative_field` | 负向提示词 | 留空=该工作流不支持负面词 |
| `seed_node` / `seed_field` | 随机种子 | 留空=节点无种子输入（如 r2v），界面仍可填但不生效 |
| `ratio_node` / `ratio_field` | 画面比例 | 留空=不支持 |
| `duration_node` / `duration_field` | 时长（秒） | 文生图留空 |
| `resolution_node` / `resolution_field` | 生成分辨率（megapixels） | 文生图留空 |
| `steps_node` / `steps_field` | 采样步数 | 留空=该工作流步数固定，界面隐藏步数字段 |
| `width_node` / `height_node` / `width_field` / `height_field` | 直接注入像素宽高 | **仅图片类工作流用**（zimage） |
| `size_baseline` | 图片类按此基准换算像素 | zimage 用 `1280` |

### 节点 ID 的两种格式

- **顶层节点**：纯数字，如 `115`、`136`、`133`
- **子图内的节点**：`父节点:子节点`，如 `105:104`、`57:27`
  例：`t2v.json` 里 `MiniMaxH3ImageToVideo` 在子图 105 内，ID 写作 `105:104`

### 本机实测节点对照表（照抄前务必用第 4 节方法重新核对）

| 配置名 | 文件 | prompt | seed | ratio | duration | resolution | steps |
|---|---|---|---|---|---|---|---|
| `standard` | t2v.json | `105:104`/prompt | `105:15`/noise_seed | `115`/aspect_ratio | `105:111`/value | `115`/megapixels | `105:9`/steps |
| `turbo` | t2vt2.json | `133`/prompt | `131`/noise_seed | `115`/aspect_ratio | `135`/value | `115`/megapixels | `126`/steps |
| `r2v` | r2v.json | `136`/prompt | （无） | `115`/aspect_ratio | `132`/value | `115`/megapixels | （无） |
| `r2vt` | r2vt.json | `136`/prompt | （无） | `115`/aspect_ratio | `132`/value | `115`/megapixels | `124`/steps |
| `zimage` | zimage.json | `57:27`/text | `57:3`/seed | `57:13` width/height | （无） | （无） | `57:3`/steps |

> 注意 `zimage` 是**图片类**：用 `width_node`/`height_node` 直接注入像素，
> 比例值是简单 `W:H`（如 `16:9`），由后端按 `size_baseline: 1280` 算出实际宽高。
> 视频类的 `ratio_options` 值则是 ComfyUI 原生字符串（如 `16:9 (Widescreen)`）。**两者格式不同，别混用。**

---

## 4. 新机器适配流程（照做即可）

### 步骤 1：从 ComfyUI 导出「API 格式」工作流

1. 在那台机器的 ComfyUI 网页里搭好工作流（或打开现成的）
2. 开启开发者模式：设置（齿轮）→ `Enable Dev Mode Options`
3. 菜单 → **Save (API Format)** → 得到 API 格式 JSON
4. 存到 `workflows/<文件名>.json`

### 步骤 2：查清节点 ID（**不要靠猜**）

用随附工具直接列出所有节点：

```bash
python inspect_workflow.py workflows/t2vt2.json
```

输出示例：

```
   115  ResolutionSelector        ResolutionSelector
   126  BasicScheduler            BasicScheduler
   133  MiniMaxH3ImageToVideo     MiniMaxH3ImageToVideo
   135  PrimitiveFloat            Float (duration)
```

从中找到你要注入的**那几个**节点：
- 提示词 → `CLIPTextEncode` 或 `MiniMaxH3ImageToVideo` / `MiniMaxH3ReferenceToVideo`
- 种子 → `RandomNoise`
- 步数 → `BasicScheduler`（字段 `steps`）
- 比例 / 分辨率 → `ResolutionSelector`（字段 `aspect_ratio` / `megapixels`）
- 时长 → `PrimitiveFloat`（标题常为 `Float (duration)`，字段 `value`）
- 图片宽高 → `EmptySD3LatentImage`（字段 `width` / `height`）

> 想确认某个节点有哪些**输入字段名**，看该节点 `inputs` 里的 key，或查
> `http://127.0.0.1:8188/object_info/<class_type>` 的 `required` / `optional`。

### 步骤 3：在 config.yaml 里注册

1. `comfyui.workflow_options` 加一行 `"显示名": "配置名"`
2. `comfyui.workflows.<配置名>.file` 指向刚存的 JSON
3. `comfyui.workflows.<配置名>.inject` 按步骤 2 查到的 ID 逐个填
4. 图片类工作流额外加 `media_type: image`
5. `comfyui.mode_workflows` 里把该配置名挂到对应模式

### 步骤 4：验证

1. 重启控制台服务
2. 打开网页 → 切到该流程 → 确认芯片（步数/比例等）正常显示
3. 提交一个**最短时长 + 最低分辨率**的任务
4. 看 ComfyUI 是否收到、参数是否生效（可在 ComfyUI 历史里看实际 prompt）

---

## 5. 常见错误与排查

| 现象 | 原因 | 排查 |
|---|---|---|
| 界面上某个参数芯片不显示 | `inject` 里对应 `*_node` 留空，或 `*_options` 为空 | 检查 config 该工作流的 inject 段 |
| 提交后参数没生效 | 节点 ID 或字段名填错 | 用 `inspect_workflow.py` 重新核对 |
| ComfyUI 报 node not found | 用了 UI 格式 JSON，或节点 ID 不存在 | 确认导出的是 **API 格式** |
| 提示词进去了但步数没变 | `steps_node` 指向的节点不是 `BasicScheduler` | 核对 `class_type` 与字段名 |
| 子图节点注入失败 | ID 写成了顶层格式 | 子图内节点要写 `父:子`（如 `105:104`） |
| 文生图比例错乱 | 用了视频类的 `16:9 (Widescreen)` 格式 | 图片类 `ratio_options` 值应为 `16:9` |
| 万能参考用了 Turbo 模型 | `MiniMaxH3ReferenceToVideo` 无 Turbo 版 | 参考生视频只能走 `r2v`/`r2vt` |
| 界面样式全丢/错乱 | `web/style.css` 没一起拷 | 确认 web 目录三个文件齐全 |

---

## 6. 后端注入逻辑在哪看

完整实现见 `core/comfy.py`（约 950 行），关键函数：

- `build_prompt_graph()`：读工作流 JSON → 按 inject 配置把参数写进对应节点 `inputs`
- 图片类走 `width_node`/`height_node` + `size_baseline` 换算
- 视频类的时长经 `ComfyMathExpression` 节点换算成帧数
- 图生/首尾帧模式：动态插入 `LoadImage` 节点，缩放算法由 `comfyui.image_upscale_method` 控制

改新机器时，**一般不需要改 `core/comfy.py` 的代码**，只改 `config.yaml` 的 `inject` 段即可。
只有当新工作流的结构差异太大（比如参数不是直接注入而是要经中间节点换算）时才动代码。
