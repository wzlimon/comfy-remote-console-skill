# 网页界面结构 & API 参数格式

> 新机器照这份文档生成界面与后端，字段才对得上。
> 参照实现：`server_reference.py`（真实可运行版）、`web/index.html`、`web/app.js`、`web/style.css`。

---

## 1. 界面结构（web/index.html）

```
┌─ 登录页 #login（需要密码时显示）
│
└─ 主体 #app
   ├─ 顶栏 .topbar：品牌「视频生成台」+ 自检状态 #health + 退出按钮 #logoutBtn
   ├─ 标签页 .tabs：新建 / 进行中(带角标) / 历史
   │
   ├─ 页「新建」#page-new
   │   ├─ 模式分段 #modeSeg（5 个）：文字生成 | 图片生成 | 首尾帧 | 万能参考 | 文生图
   │   ├─ 首/尾帧图槽 #imgRow（i2v/flf 显示）：fileFirst / fileLast
   │   ├─ 图库按钮 #imgGalRow：pickGalleryFirst / pickGalleryLast
   │   ├─ 参考图槽 #refRow（r2v 显示）：fileRef0 / fileRef1 / fileRef2（最多 3 张）
   │   ├─ 图库按钮 #refGalRow：pickGalleryRefs（可多选，最多 3）
   │   ├─ 项目专库输入 #projectInput（留空=默认库）
   │   ├─ 提示词 #prompt（textarea，带字数统计 #charCount、清空 #clearPrompt）
   │   ├─ 参数折叠区 #paramBox（<details>）
   │   │   ├─ 流程        #wfField    → 芯片容器 #wfChips
   │   │   ├─ 步数        #stepsField → #stepChips
   │   │   ├─ 时长        #durField   → #durChips
   │   │   ├─ 生成分辨率   #resField   → #resChips
   │   │   ├─ 画面比例     #ratioField → #ratioChips
   │   │   ├─ 超分到1080P  #upscale（开关）
   │   │   └─ 随机种子     #seed（留空=随机）
   │   └─ 提交按钮 #submitBtn + 提示 #submitMsg
   │
   ├─ 页「进行中」#page-running：#runList（活跃任务卡片、进度条、取消）
   ├─ 页「历史」#page-history：搜索 #kw、导出、统计 #stats、#hisList、加载更多
   └─ 图库弹窗 #galleryModal：#galleryGrid（多选，按项目过滤）
```

### 关键 DOM id（前端 JS 依赖，改名会导致界面失效）

| 用途 | id |
|---|---|
| 模式按钮 | `#modeSeg .seg-item`，`data-mode` = t2v/i2v/flf/r2v/t2i |
| 首帧 / 尾帧 / 参考图 | `#fileFirst` `#fileLast` `#fileRef0` `#fileRef1` `#fileRef2` |
| 项目专库 | `#projectInput` |
| 提示词 | `#prompt` `#charCount` `#clearPrompt` |
| 参数芯片 | `#wfChips` `#stepChips` `#durChips` `#resChips` `#ratioChips` |
| 参数字段容器 | `#wfField` `#stepsField` `#durField` `#resField` `#ratioField` `#upscaleField` |
| 超分 / 种子 | `#upscale` `#seed` |
| 提交 | `#submitBtn` `#submitMsg` |
| 登录 / 退出 | `#login` `#pwd` `#loginBtn` `#loginErr` `#logoutBtn` |
| 自检 | `#health` |
| 列表 | `#runList` `#hisList` `#stats` `#runBadge` `#moreBtn` `#kw` |
| 图库弹窗 | `#galleryModal` `#galleryGrid` `#galleryOk` `#galleryCancel` |

> ⚠️ 三个前端文件必须齐全：`index.html` + `app.js` + **`style.css`**。
> 少了 `style.css` 界面会样式全丢、排版错乱（这是新机器最常见的「界面错误」）。

---

## 2. 界面由 `/api/options` 驱动（不要写死）

**前端所有选项都来自后端 `/api/options`，改 `config.yaml` 即改界面**，前端不硬编码任何选项。

`/api/options` 返回结构：

```json
{
  "workflows": [
    {"name": "turbo", "label": "Turbo加速", "steps_options": [4,5,6,7,8,9,10,11,12], "steps_default": 6}
  ],
  "workflow_default": "turbo",
  "mode_workflows": {"t2v": ["standard","turbo"], "r2v": ["r2v","r2vt"], "t2i": []},
  "ratios": ["16:9 横屏", "9:16 竖屏", "1:1 方形"],
  "ratio_default": "9:16 竖屏",
  "resolutions": ["480P", "720P", "1080P"],
  "resolution_default": "480P",
  "durations": [4,5,6,8,10,12,15,20,25,30],
  "duration_default": 5,
  "modes": {"t2v":"文生视频","i2v":"图生视频","flf":"首尾帧","r2v":"万能参考","t2i":"文生图"},
  "upscale_enabled": true,
  "netdisk_enabled": true
}
```

前端渲染规则（`app.js`）：

- `loadOptions()`：拉 options → 渲染时长/分辨率/比例芯片 → `refreshWfChips()` → `renderSteps()`
- `refreshWfChips()`：**按当前模式**从 `mode_workflows[mode]` 取可用流程，过滤后渲染；列表为空则隐藏「流程」字段
- `renderSteps()`：`steps_options.length <= 1` 时**隐藏步数字段**（如 standard 固定 20 步）
- 切换模式时 `hidden` 控制：
  - `i2v`/`flf`：显示图片槽，隐藏比例（比例跟随图片）
  - `flf`：额外显示尾帧
  - `r2v`：显示 3 个参考图槽（比例仍显示，由 ResolutionSelector 控制）
  - `t2i`：隐藏时长 / 分辨率 / 超分

---

## 3. 提交接口 `POST /api/submit`（multipart/form-data）

### 字段表

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | text | ✅ | 提示词，空则 400 |
| `mode` | text | ✅ | `t2v`/`i2v`/`flf`/`r2v`/`t2i`，默认 `t2v`，非法则 400 |
| `workflow` | text | | 配置名（如 `turbo`）。`r2v` 模式下传 `turbo`/`r2vt` 会切到 `r2vt`；`t2i` 强制走 `zimage` |
| `ratio` | text | | 画面比例（`ratio_options` 的 key）。`i2v`/`flf` 会被置空（跟随图片） |
| `duration` | int | | 秒，默认 5 |
| `resolution` | text | | `resolution_options` 的 key（480P/720P/1080P），默认 `480P` |
| `steps` | int | | 步数；不传则用工作流默认 |
| `seed` | int | | 随机种子；**留空=每次随机** |
| `upscale` | 0/1 | | 是否超分到 1080P，默认 1 |
| `project` | text | | 项目专库名，正则 `^[\w-]+$`，留空=默认库 |
| `negative` | text | | 负向提示词（工作流支持时才生效） |
| `first_image` | file | 见下 | 首帧图片文件 |
| `first_image_name` | text | 见下 | 复用图库：相对 uploads 的路径 |
| `last_image` | file | flf | 尾帧图片文件 |
| `last_image_name` | text | flf | 复用图库尾帧 |
| `ref_0` `ref_1` `ref_2` | file | r2v | 参考图文件（最多 3 张） |
| `ref_0_name` `ref_1_name` `ref_2_name` | text | r2v | 复用图库参考图（相对路径） |

### 各模式必填矩阵

| 模式 | 图片要求 | 比例 | 时长/分辨率/超分 |
|---|---|---|---|
| `t2v` 文字生成 | 无 | 显示 | 显示 |
| `i2v` 图片生成 | **首帧必填** | 隐藏（跟随图片） | 显示 |
| `flf` 首尾帧 | **首帧 + 尾帧必填** | 隐藏 | 显示 |
| `r2v` 万能参考 | **至少 1 张，最多 3 张**参考图 | 显示 | 显示 |
| `t2i` 文生图 | 无 | 显示 | 隐藏 |

### 图片两种传法（互斥，服务端优先取 `_name`）

1. **复用图库**：传 `<slot>_name` = `rel_path`（`/api/refs` 返回的 `rel_path`，如 `proj/chars/a.png`）
2. **新上传**：传文件字段 `<slot>`（如 `ref_0`）

服务端 `_resolve_img()` 先查 `_name` 对应的安全路径，命中就用；否则接收新上传。
`_name` 含 `..` / 绝对路径 / 盘符 / 非白名单后缀 → 视为无效，回退到新上传（防穿越）。

### 返回

```json
{"ok": true, "id": 42, "ahead": 2}
```
`ahead` = 前面排队几个任务。

### curl 示例（Bearer 令牌）

```bash
curl -X POST http://HOST/api/submit \
  -H "Authorization: Bearer $H3_DIRECTOR_TOKEN" \
  -F "prompt=黄昏海边，女孩逆光奔跑" \
  -F "mode=t2v" -F "workflow=turbo" \
  -F "ratio=9:16 竖屏" -F "duration=5" -F "resolution=480P" -F "steps=6" \
  -F "upscale=1" -F "project=tianbao_nimingshu"
```

---

## 4. 全部 API 一览

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| POST | `/api/login` | — | body JSON `{password}`，正确则写 session |
| GET | `/api/me` | — | `{need_password, logged_in}` |
| POST | `/api/logout` | — | 清 session，返回新 cookie 使旧会话失效 |
| GET | `/api/options` | ✅ | 表单所有选项（见第 2 节） |
| GET | `/api/refs?project=` | ✅ | 图库清单，带 project 则递归该项目目录 |
| POST | `/api/upload` | ✅ | 批量上传：`files`(多) + `project` + `subdir`，同名覆盖 |
| POST | `/api/submit` | ✅ | 提交生成任务（见第 3 节） |
| GET | `/api/tasks?limit&offset&keyword&status` | ✅ | 列表，含 `actives`/`queue`/`stats` |
| GET | `/api/task/<id>` | ✅ | 单条记录 |
| POST | `/api/task/<id>/cancel` | ✅ | 取消 |
| POST | `/api/task/<id>/delete` | ✅ | 删除记录 + 成品；共享图仅在无人引用时删 |
| GET | `/api/health` | ✅ | ComfyUI / Topaz 自检 |
| GET | `/api/export.csv` | ✅ | 导出 CSV（带 BOM，Excel 不乱码） |
| GET | `/video/<path>` | ✅ | 成品视频，支持 Range、?dl=1 下载 |
| GET | `/image/<path>` | ✅ | 成品图片 / 封面 |
| GET | `/thumb/<path>` | ✅ | 缩略图 |
| GET | `/upload/<path>` | ✅ | 图库原图（走 `safe_upload_path` 防穿越） |

✅ = 需 `need_login`（网页 cookie 或 Bearer 令牌二选一）

### 认证

- **网页密码**：`config.yaml` 的 `server.password`；登录后 cookie session，密钥持久化在 `data/secret.key`
- **导演令牌**：环境变量 `H3_DIRECTOR_TOKEN`，请求头 `Authorization: Bearer <token>`
  用 `secrets.compare_digest` 防时序侧信道

### 状态码约定

| 码 | 含义 |
|---|---|
| 200 | 成功 |
| 400 | 参数非法（空提示词、非法 project/subdir、缺图） |
| 401 | `{"error":"need_login"}` —— 前端收到即弹登录页 |
| 403 | 密码错误 |
| 404 | 记录 / 文件不存在 |
| 413 | 超过 `max_upload_mb` |

---

## 5. 前端要点（改界面时别踩）

- `assetUrl(base, rel)`：相对路径含 `/` 时要**逐段 encodeURIComponent**，
  整串 encode 会把 `/` 变成 `%2F`，导致 Flask `<path:name>` 路由 404
- 轮询：`refresh()` 每 3 秒拉 `/api/tasks?limit=1`，`document.hidden` 时跳过；自检 30 秒一次
- 任务卡片：状态标签、进度条、阶段文字、成品预览（图片走 `/image/`，视频走 `/video/` + 封面 `/thumb/`）
- 网盘提示：`netdisk_path` 以「投递失败」开头显示为失败样式
- 删除确认、取消、复用提示词都走 `data-act` 委托
