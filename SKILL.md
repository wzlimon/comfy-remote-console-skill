---
name: comfy-remote-console
description: "在一台装了 ComfyUI 的 Windows 电脑上，搭建一个手机可远程访问的网页控制台（表单提交→本机 ComfyUI 生成→网页取片），并具备：双认证（网页密码 + 导演令牌 Bearer 供 CODEX/自动化调度）、项目专库资产管理（/api/refs 递归、/api/upload 安全批量上传、同名覆盖）、Cloudflare trycloudflare 公网隧道（免费、无流量限制）。当用户要「让另一台电脑也能手机远程操控 ComfyUI」「把本地 AI 服务做成手机网页控制台」「给自动化/CODEX 开放批量上传资产接口」时使用。"
description_zh: "为本地 ComfyUI 搭建手机可远程访问的网页控制台（双认证 + 项目专库 + trycloudflare 隧道）"
description_en: "Mobile-accessible remote web console for a local ComfyUI (dual auth + project asset library + trycloudflare tunnel)"
version: 1.2.0
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Grep
  - Glob
  - AskUserQuestion
trigger: ["手机远程", "远程控制台", "comfy 手机", "外网访问 comfyui", "CODEX 上传资产", "项目专库", "trycloudflare comfyui", "cloudflared", "ngrok comfyui", "remote console", "comfy-mobile-studio"]
---

# ComfyUI 手机远程控制台框架

把「本机 ComfyUI（8188 端口）」包装成一个**手机浏览器就能用的网页控制台**：表单填提示词 → 队列提交 → 本机生成 → 网页取片/下载。已在本机（RTX 4070 Ti SUPER / Win / ComfyUI 0.30.0）长期运行验证。

本 skill 提供**可复用的框架与经过实测的核心代码模板**，不是某个具体产品的源码。另一台机器上的 WorkBuddy 加载后，照着把后端接到它自己的 ComfyUI 工作流即可。

---

## 一、架构总览

```
手机/远端浏览器 ──HTTP──> Flask 控制台(:8790) ──本地 HTTP──> ComfyUI(:8188)
        │                         │
        │                         ├─ 双认证：网页密码(cookie) / 导演令牌(Bearer)
        │                         ├─ 项目专库：data/uploads/<project>/<subdir>/
        │                         └─ 成品：data/outputs/<project>/{raw,upscaled,thumbs}
        ▼
   Cloudflare trycloudflare 临时隧道（可选，免费 / 无流量限制 / 地址重启即变）
        ▼
   任意网络下的手机访问
```

**两个认证通道（关键设计）：**
- **网页密码 + cookie session**：人用手机登录。会话密钥持久化到 `data/secret.key`，重启后手机不用重输。
- **导演令牌 `H3_DIRECTOR_TOKEN`（Bearer）**：给 CODEX / 脚本 / 自动化调度用，无 cookie 也能调 API。令牌**只放 Windows 用户环境变量**（HKCU\Environment），**绝不写进配置文件**，避免入库泄露。

---

## 一点五、references 文件清单（部署前先看这张表）

| 文件 / 目录 | 用途 | 新机器怎么用 |
|---|---|---|
| **`server_reference.py`** | **真实可运行的服务端**（全部接口 + 安全校验 + 退出登录） | **直接当 `server.py` 用** |
| `server_template.py` | 精简教学版 | 仅作阅读参考，功能不全，**勿用于部署** |
| **`core/`** | 后端核心：`comfy.py`（工作流注入）、`pipeline.py`、`store.py`、`topaz.py`、`config.py`、`delivery.py` | **整目录拷**，一般不用改代码 |
| **`workflows/`** | 6 个 ComfyUI **API 格式**工作流 JSON | **整目录拷**，再换成新机器自己导出的 |
| **`web/`** | 前端三件套 `index.html` + `app.js` + **`style.css`** | **整目录拷，三个都要** |
| **`config.example.yaml`** | 完整配置模板（含 5 个工作流的节点映射） | 复制为 `config.yaml` 后据实填 |
| **`inspect_workflow.py`** | 查工作流节点 ID / 输入字段 | **新机器适配必用** |
| **`WORKFLOWS.md`** | 工作流命名、位置、节点映射、适配流程、排错 | **必读** |
| **`API_AND_UI.md`** | 界面结构、DOM id、提交参数格式、API 一览 | **必读**（做界面前） |
| `run_server.py` | 从注册表读令牌后启动 server | 直接拷 |
| `upload_assets.py` | CODEX 批量上传 CLI | 直接拷 |
| `NEW_MACHINE_GUIDE.md` | 第二台机器完整部署教程（含域名/隧道） | 按它一步步做 |
| `CLOUDFLARE.md` | 隧道方案（trycloudflare / 固定域名） | 需要外网时看 |
| `launch_scripts.md` | 各类 .bat 启动脚本 | 按需取 |

> 部署顺序建议：先整套拷过去跑通 → 再按 `WORKFLOWS.md` 换工作流 → 最后按 `API_AND_UI.md` 微调界面。

---

## 二、在新机器上部署（步骤）

1. **前置**：Windows + Python 3.10+；本机 ComfyUI 已在 `127.0.0.1:8188` 跑起来；（可选）已装 `cloudflared`（外网访问用，见第七节）。
2. 把本 skill `references/` 下的代码落到新目录（如 `D:\comfy-mobile-studio\`）：
   - `server_reference.py` → **直接用它做 `server.py`**（真实可运行版，已含全部接口与安全校验）
     - `server_template.py` 只是精简教学版，功能不全，**实际部署请用 `server_reference.py`**
   - `core/`（**整目录**）：`comfy.py`（工作流注入核心）、`config.py`、`pipeline.py`、`store.py`、`topaz.py`、`delivery.py`
   - `workflows/`（**整目录**）：6 个 ComfyUI API 格式工作流 JSON，**必须一起拷**，否则界面没有可用流程
   - `web/`（**整目录，三个文件缺一不可**）：`index.html` + `app.js` + **`style.css`**
     - 少了 `style.css` 界面会样式全丢、排版错乱
   - `config.example.yaml` → 复制为 `config.yaml` 并据实填写（见第四、五节）
   - `run_server.py`（从注册表读令牌后启动，解决「工具环境没继承用户环境变量」问题）
   - `upload_assets.py`（给 CODEX 的批量上传 CLI）
   - `inspect_workflow.py`（查工作流节点 ID 的工具，新机器适配必用）
3. **按 `references/WORKFLOWS.md` 适配工作流**——这是决定界面/参数对不对的关键步骤：
   - 从新机器的 ComfyUI 导出「API 格式」工作流 JSON 到 `workflows/`
   - 用 `python inspect_workflow.py workflows/xxx.json` 查清节点 ID
   - 把 ID 填进 `config.yaml` 的 `comfyui.workflows.<name>.inject` 段
   - **不要照抄本机的节点 ID**，那是 MiniMax H3 / Z-IMAGE 工作流专属的
3. 建虚拟环境装依赖：`python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`
   - requirements：`flask>=3.0 requests>=2.31 pyyaml>=6.0 waitress>=3.0`
4. 设令牌（管理员 PowerShell）：
   ```
   [Environment]::SetEnvironmentVariable("H3_DIRECTOR_TOKEN", (New-Guid).ToString()+"x", "User")
   ```
   记下令牌值，给 CODEX / 远端脚本用。
5. 开放防火墙（让局域网手机能连）：用 `开放端口.bat`（见 references，本质是 `netsh advfirewall firewall add rule ... localport=8790`）。
6. 启动：双击 `启动.bat`（内部跑 `run_server.py` → 读注册表令牌 → `server.py`）。手机连同一 WiFi 打开 `http://<本机IP>:8790`。
7. **要外网访问**：另开一个窗口 `cloudflared tunnel --url http://localhost:8790`，把生成的 `https://xxxx.trycloudflare.com` 发给手机（免费、无流量限制、地址重启即变，见第七节）。

> 完整脚本见 `references/launch_scripts.md`。

---

## 三、核心代码模板用法

`references/server_template.py` 是**自包含、可直接跑**的最小实现，已包含全部经过验证的关键模式：
- `need_login` + `is_director_request`：双认证，Bearer 令牌用 `secrets.compare_digest` 防时序侧信道。
- `safe_upload_path` / `_safe_subdir`：防 `..`、绝对路径、盘符、非白名单后缀穿越。
- `/api/refs?project=`：递归扫描项目专库，返回 `{name, rel_path, project, type, url, size, ts}`。
- `POST /api/upload`：Bearer 或 cookie 认证，表单 `files`(多文件)+`project`+`subdir`，**同名覆盖**（确定性，自动化重跑不产生 `_xxxxxx` 垃圾副本）。
- `/api/submit`：接收 `project` 与相对路径引用（`ref_0_name=project/subdir/file.png`），落库后交给后台 worker。
- 文件路由 `/upload /video /image /thumb` 全部走 `safe_upload_path`/`resolve_asset` 安全校验。
- **后端接入点**：模板里的 `submit_to_comfyui()` 是占位函数，注释写清了怎么改成「POST ComfyUI /prompt + 轮询 /history」的真实调用。另一台机器照此接到自己的 workflow。

> **关于 `core/` 与 `workflows/`（重要，别误解）：**
> 早期版本建议「不要复制 core/ 和 workflow JSON」，结果新机器没有任何可参照的实现，
> 只能凭空猜，生成的界面和参数都是错的。**现在本 skill 已把完整可运行实现全部带上**：
> - `core/` + `workflows/` + `web/` + `server_reference.py` = 本机长期运行的真实版本
> - 新机器的正确做法：**先整套拷过去跑通**，再按 `WORKFLOWS.md` 把工作流换成自己的
> - 也就是说：`core/` 的注入逻辑一般**不用改代码**，只改 `config.yaml` 的 `inject` 节点映射
> - 只有当新工作流结构差异极大（参数要经中间节点换算等）时才动 `core/comfy.py`
>
> 别从零手写——照着真实实现改，比凭空写靠谱得多。

---

## 四、config.yaml 要点

完整模板见 `references/config.example.yaml`（含全部注释与 5 个工作流的节点映射）。核心骨架：

```yaml
server:
  host: "0.0.0.0"          # 0.0.0.0=允许局域网手机访问
  port: 8790
  password: "改一个强密码"   # 开外网穿透前必填
  session_days: 30
  max_upload_mb: 200        # 自动化批量传大图放宽

comfyui:
  host: "127.0.0.1"
  port: 8188

  # 界面显示名 -> 配置名
  workflow_options:
    "Turbo加速": "turbo"
    "文生图": "zimage"
  workflow_default: "turbo"

  # 每个模式下可选的流程；留空=前端隐藏该字段
  mode_workflows:
    t2v: ["standard", "turbo"]
    r2v: ["r2v", "r2vt"]
    t2i: []

  workflows:
    turbo:
      file: "workflows/t2vt2.json"     # 必须是 API 格式 JSON
      inject:                          # ★ 网页参数 -> 工作流节点的映射
        prompt_node: "133"             #    顶层节点=纯数字；子图内=父:子(105:104)
        prompt_field: "prompt"
        seed_node: "131"
        seed_field: "noise_seed"
        steps_node: "126"              #    留空=不支持，前端自动隐藏该字段
        steps_field: "steps"
        steps_options: [4,5,6,7,8,9,10,11,12]
        ratio_node: "115"
        ratio_field: "aspect_ratio"
        duration_node: "135"
        duration_field: "value"
        resolution_node: "115"
        resolution_field: "megapixels"
    zimage:
      file: "workflows/zimage.json"
      media_type: image                # ★ 图片类必须标
      inject:
        width_node: "57:13"            #   图片类直接注入像素，不用 megapixels
        height_node: "57:13"
        size_baseline: 1280

runtime:
  uploads_dir: "data/uploads"
  outputs_dir: "data/outputs"
  db_file: "data/tasks.db"
```

**关键：`inject` 段的节点 ID 是机器相关的，必须用 `inspect_workflow.py` 在新机器上重新查。**
详细规则、字段全表、常见错误见 **`references/WORKFLOWS.md`**。

---

## 四之二、网页界面与提交参数格式

**生成界面前必读 `references/API_AND_UI.md`**，里面含：

- 界面完整结构图 + **关键 DOM id 清单**（前端 JS 依赖这些 id，改名即失效）
- 「界面由 `/api/options` 驱动」的机制：**改 config.yaml 即改界面，前端不硬编码任何选项**
- `POST /api/submit` 的**完整字段表**与各模式必填矩阵
- 全部 API 一览 + 状态码约定

界面三要点（最容易错）：

1. **`web/` 三个文件必须齐全**：`index.html` + `app.js` + **`style.css`**。少 `style.css` 界面直接崩。
2. **选项全部来自 `/api/options`**，不要写死在前端。流程芯片按 `mode_workflows[mode]` 过滤。
3. **提交用 multipart/form-data**，图片有两种传法：
   - 复用图库：传 `<slot>_name` = `rel_path`（如 `proj/chars/a.png`）
   - 新上传：传文件字段 `<slot>`（如 `ref_0`）
   - 服务端优先取 `_name`；含 `..`/绝对路径/盘符则判无效回退上传（防穿越）

---

## 五、给 CODEX / 自动化开放批量上传

脚本 `references/upload_assets.py`（纯标准库）：
```bash
python upload_assets.py --project tianbao_nimingshu \
    --subdir characters/model_sheets chr_li.png "scene_*.png"
```
- 令牌优先级：`--token` > 环境变量 `H3_DIRECTOR_TOKEN` > 注册表。
- 上传后 `/api/refs?project=xxx` 立即可见，提交时用 `rel_path` 引用。
- 安全边界与网页端一致：仅 png/jpg/jpeg/webp；project 限 `[\w-]+`；subdir 禁 `..`/绝对路径/盘符。

---

## 六、安全清单（开外网前必看）

- [ ] `server.password` 已设强密码（外网穿透**必须**）。
- [ ] `H3_DIRECTOR_TOKEN` 仅存于 Windows 用户环境变量，未进任何配置文件 / git。
- [ ] 上传与文件路由全部走 `safe_upload_path`，杜绝路径穿越与任意文件读取。
- [ ] `max_upload_mb` 合理（防大文件打满磁盘）。
- [ ] trycloudflare / quick tunnel 地址随机且重启即变；敏感场景考虑固定隧道（Cloudflare 自有域名）或自建 frp。

---

## 七、Cloudflare trycloudflare 临时隧道（免费、无流量限制）

- 命令：`cloudflared tunnel --url http://localhost:8790` → 得到随机 `https://xxxx.trycloudflare.com`，**免费、无需登录、无流量限制**，但**每次重启地址都变**，需重新发给手机。
- 安装：见 `references/CLOUDFLARE.md`（winget / 官方下载 / GitHub release 任选）。
- 固定域名（长期使用）：需 Cloudflare 账号 + 自有域名，`cloudflared tunnel create` 后地址不变，详见 `references/CLOUDFLARE.md` 第五节。
- 替代：frp / 路由器端口转发（有公网 IP 或中转服务器时自建，地址可控）。

> 注：原 ngrok 方案因免费版有每月流量上限，已弃用；改成 trycloudflare 解决流量限制问题。

---

## 八、关于「云端让另一台机器自己学」

WorkBuddy **没有**跨物理机器自动部署/自学的机制——每台机器是独立实例，本会话也碰不到另一台电脑磁盘。可转移的只有**本 skill 这个可移植知识包**。两种转移方式：

1. **直接拷文件夹**（最快）：把本 skill 目录 `comfy-remote-console/` 整个复制到另一台机器的
   `C:\Users\<用户名>\.workbuddy\skills\comfy-remote-console\`，重启 WorkBuddy 即可在可用 skills 里看到，让它照着部署。
2. **推 GitHub 后安装**：把 skill 推进你的仓库（如 `wzlimon/comfy-remote-console-skill`），另一台机器用 WorkBuddy 的「从市场/Git 安装 skill」功能拉取。

两种方式都不需要你手写代码——那台机器的 WorkBuddy 加载 skill 后，按 SKILL.md 的步骤把后端接到自己的 ComfyUI 即可。
