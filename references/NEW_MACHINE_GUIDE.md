# 另一台电脑部署教程：手机远程 ComfyUI 控制台

> 适用场景：你在第二台装了 ComfyUI 的 Windows 电脑上，想复刻「手机网页远程操控 + 项目专库 + Cloudflare 固定域名隧道」这套能力。
> 本教程配合 `comfy-remote-console` SKILL 使用。你在本机（第一台）已验证过的全部关键模式（双认证、防穿越、项目专库、批量上传、退出登录、固定隧道）都在这套代码里。

---

## 0. 前置条件（第二台电脑）

- Windows 10/11，已装 **Python 3.10+**（建议 3.13）
- 本机 **ComfyUI 已在运行**，且能访问 `http://127.0.0.1:8188`
- 有 Git（用于克隆 SKILL）或直接能下载 ZIP
- **域名已就绪**：第一台已在同一 Cloudflare 账号的 `cnun.com` 下建好子域 `comfy2.cnun.com`（DNS 路由 + tunnel `comfy2-console` 已建好，Tunnel ID `735569eb-0731-4e39-ae00-996d8e9bc375`）。第二台**直接复用**即可，无需再碰 Cloudflare 后台——只需从第一台取得 `comfy2-console` 的凭证文件（见第 3 节方案 A）。
- 能科学上网（Cloudflare / GitHub 访问）

---

## 1. 下载并安装 SKILL

### 方式 A：Git 克隆（推荐，以后可 `git pull` 更新）

打开 PowerShell / CMD，执行：

```bat
git clone https://github.com/wzlimon/comfy-remote-console-skill.git "%USERPROFILE%\.workbuddy\skills\comfy-remote-console"
```

### 方式 B：下载 ZIP

1. 浏览器打开 https://github.com/wzlimon/comfy-remote-console-skill
2. 点 `Code` → `Download ZIP`
3. 解压，把里面的 `comfy-remote-console-skill` 文件夹重命名为 `comfy-remote-console`
4. 放到 `C:\Users\<你的用户名>\.workbuddy\skills\comfy-remote-console\`

### ⚠️ 两个目录要分清（最容易搞混）

| 目录 | 是什么 | 是不是 git 仓库 | 用 git pull 吗 |
|---|---|---|---|
| `C:\Users\<用户名>\.workbuddy\skills\comfy-remote-console\` | **SKILL 知识包**（给 WorkBuddy 学 + 模板文件源） | ✅ 是（clone 出来的） | ✅ **在这里 pull** |
| `D:\comfy-mobile-studio\` | **实际运行的控制台项目** | ❌ 不是 | ❌ pull 无效 |

**`git pull` 只更新 SKILL 目录，不会自动更新正在跑的项目目录。**
所以完整更新是两步：先 `git pull` 拉模板，再把模板同步进项目目录（见下面）。

### 以后怎么更新（git pull）

```bat
cd /d %USERPROFILE%\.workbuddy\skills\comfy-remote-console
git pull
```

看到 `Already up to date.` 或列出改动文件即成功。

然后把新版同步进项目目录 —— **双击 `D:\comfy-mobile-studio\sync_from_skill.bat`** 即可
（这个 bat 来自 SKILL 的 `references\`，放在项目根运行）。

它会：
- 覆盖 `core\`、`workflows\`、`web\`、`server.py`、`run_server.py`、`upload_assets.py`、`inspect_workflow.py`
- **保留你改过的 `config.yaml`**（不会被冲掉，这是故意的）
- 若 `config.yaml` / `cloudflared-config.yml` 不存在才从模板新建

同步完重启控制台生效：

```bat
cd /d D:\comfy-mobile-studio
.venv\Scripts\python.exe run_server.py
```

> **如果你是 U 盘拷贝安装的（方式 B）**：那台机器上没有 git 仓库，`git pull` 用不了。
> 要么在第一台重新下载 ZIP 拷过去，要么干脆把第一台整个 `D:\comfy-mobile-studio\` 目录重新拷一遍覆盖
> （同样注意别覆盖你自己改的 `config.yaml`）。

### 验证安装

- 关闭并重新打开 WorkBuddy（必须重启，skill 才加载）
- 在可用 skills 列表里应能看到 `comfy-remote-console`
- 你也可以直接按本教程手动部署（不依赖 WorkBuddy 也能跑，SKILL 只是把知识包固化了）

> 想偷懒：装好 skill 后，直接对那台机器的 WorkBuddy 说「按 comfy-remote-console skill 帮我部署手机远程控制台，ComfyUI 在 127.0.0.1:8188」，它会照着 SKILL.md 把下面 2~3 步大部分自动做完。但本教程给你的是可独立照做的版本。

---

## 2. 初始化控制台项目（第二台电脑，本机）

### 2.1 准备项目目录

```bat
mkdir D:\comfy-mobile-studio
cd /d D:\comfy-mobile-studio
```

### 2.2 把 SKILL 里的模板文件复制进来

从克隆/解压得到的 skill 目录，复制以下文件到项目根（`D:\comfy-mobile-studio\`）：

```
【必拷 · 整目录】
  references\core\        ->  D:\comfy-mobile-studio\core\        （6 个后端模块）
  references\workflows\   ->  D:\comfy-mobile-studio\workflows\   （6 个 API 格式工作流 JSON）
  references\web\         ->  D:\comfy-mobile-studio\web\         （index.html + app.js + style.css，三个都要）

【必拷 · 单文件】
  references\server_reference.py  ->  D:\comfy-mobile-studio\server.py   ← 用这个！
  references\run_server.py        ->  D:\comfy-mobile-studio\run_server.py
  references\config.example.yaml  ->  D:\comfy-mobile-studio\config.yaml
  references\upload_assets.py     ->  D:\comfy-mobile-studio\upload_assets.py
  references\inspect_workflow.py  ->  D:\comfy-mobile-studio\inspect_workflow.py

【可选 · 更新用】
  references\sync_from_skill.bat   ->  D:\comfy-mobile-studio\sync_from_skill.bat

【可选 · 隧道用】
  references\cloudflared-config.example.yml          -> D:\comfy-mobile-studio\cloudflared-config.example.yml
  references\cloudflared-config-comfy2.example.yml   -> D:\comfy-mobile-studio\cloudflared-config-comfy2.yml
```

> ⚠️ **三个易错点（照抄时最容易漏）：**
> 1. 用 **`server_reference.py`** 做 `server.py`，不要用 `server_template.py`（后者是精简教学版，功能不全）。
> 2. `web\` 里 **`style.css` 必须有**，少了它界面样式全丢、排版错乱。
> 3. `core\` 和 `workflows\` **整目录拷**，只拷单文件会缺模块/缺工作流。

> 也可以直接把整个 `references\` 目录内容拷进来，再按需改名。

### 2.3 创建虚拟环境并装依赖

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install flask requests pyyaml waitress
```

### 2.4 改 `config.yaml`

打开 `D:\comfy-mobile-studio\config.yaml`，重点改这几项：

```yaml
server:
  host: "0.0.0.0"          # 允许局域网手机访问；只本机用就改 127.0.0.1
  port: 8790
  password: "改成你自己的强密码"   # ★ 开外网前必填，这是安全底线
  session_days: 30
  max_upload_mb: 200        # 自动化/CODEX 批量上传建议 200

comfyui:
  host: "127.0.0.1"        # 第二台机器上 ComfyUI 的地址
  port: 8188

runtime:
  uploads_dir: "data/uploads"
  outputs_dir: "data/outputs"
  db_file: "data/tasks.db"
```

### 2.5 适配工作流：把网页参数接到你的 ComfyUI 工作流 ★最关键一步

> 这一步决定「界面能不能生成对」「提交的参数有没有真的传进 ComfyUI」。
> **完整规则见 `references/WORKFLOWS.md`**，这里给最短可执行路径。

好消息：现在用的是**完整实现**（`core/comfy.py`），**不需要改任何 Python 代码**，
只要把工作流 JSON 放进 `workflows/`、把节点 ID 填进 `config.yaml` 即可。

#### 步骤 1：从你这台的 ComfyUI 导出「API 格式」工作流

1. ComfyUI 网页里搭好（或打开）工作流
2. 设置（齿轮）→ 勾选 `Enable Dev Mode Options`
3. 菜单 → **Save (API Format)** → 保存为 `D:\comfy-mobile-studio\workflows\<名字>.json`

⚠️ 必须是 **API 格式**。UI 格式（带 `nodes`/`links` 数组）提交给 `/prompt` 会被拒。

#### 步骤 2：查清节点 ID

```bat
cd /d D:\comfy-mobile-studio
.venv\Scripts\python.exe inspect_workflow.py workflows\t2vt2.json
```

输出里带 `<<<` 的就是需要注入参数的节点。想看某节点有哪些输入字段：

```bat
.venv\Scripts\python.exe inspect_workflow.py workflows\t2vt2.json --inputs 133 135
```

通常对应关系：

| 网页参数 | 找这类节点（class_type） | 常用字段 |
|---|---|---|
| 提示词 | `CLIPTextEncode` / `MiniMaxH3ImageToVideo` / `MiniMaxH3ReferenceToVideo` | `prompt` 或 `text` |
| 随机种子 | `RandomNoise` / `KSampler` | `noise_seed` / `seed` |
| 步数 | `BasicScheduler` / `KSampler` | `steps` |
| 画面比例 | `ResolutionSelector` | `aspect_ratio` |
| 分辨率 | `ResolutionSelector` | `megapixels` |
| 时长 | `PrimitiveFloat`（标题常为 Float (duration)） | `value` |
| 图片宽高 | `EmptySD3LatentImage` | `width` / `height` |

#### 步骤 3：填进 config.yaml

以 `turbo` 为例（照你查到的 ID 改）：

```yaml
comfyui:
  workflow_options:
    "标准流程": "standard"
    "Turbo加速": "turbo"
    "文生图": "zimage"
  workflow_default: "turbo"
  mode_workflows:
    t2v: ["standard", "turbo"]
    i2v: ["standard", "turbo"]
    flf: ["standard", "turbo"]
    r2v: ["r2v", "r2vt"]
    t2i: []
  workflows:
    turbo:
      file: "workflows/t2vt2.json"      # ← 你的 JSON
      inject:
        prompt_node: "133"              # ← 你查到的 ID
        prompt_field: "prompt"
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
        steps_options: [4,5,6,7,8,9,10,11,12]
```

要点：

- 节点 ID 格式：顶层=`115`，子图内=`父:子`（如 `105:104`、`57:27`）
- `*_node` **留空** = 该工作流不支持这个参数 → 前端自动隐藏对应字段
- `steps_options` 只有 1 个值 → 前端隐藏步数字段
- **图片类工作流**必须加 `media_type: image`，并用 `width_node`/`height_node` + `size_baseline`，
  其 `ratio_options` 值是 `16:9` 这种简单格式（视频类是 `16:9 (Widescreen)`，**别混用**）
- 用不到的工作流：不写进 `workflow_options` 就不会出现在界面上

#### 步骤 4：验证

重启服务 → 打开网页 → 切到该流程，确认芯片（步数/比例/时长）正常显示 →
提交一个**最短时长 + 最低分辨率**的任务 → 看 ComfyUI 是否收到、参数是否生效。

> 想省事：把第一台机器 `D:\comfy-mobile-studio\` 整个目录拷到第二台同名位置，
> 然后只改 `config.yaml`（密码、ComfyUI 地址、output_dir、Topaz 路径）并**按上面重查节点 ID**
> 即可获得完整功能（含项目专库、上传 API、退出登录、6 个工作流）。
> 注意：节点 ID 与你这台的 ComfyUI 工作流绑定，换机器/换工作流后**必须重新核对**。

### 2.6 （一般不用改）前端表单

前端的三个文件（`index.html` / `app.js` / `style.css`）**直接沿用即可**，通常无需修改：

- 所有选项（流程、步数、时长、分辨率、比例）都从 `/api/options` 动态读取，
  **改 `config.yaml` 即改界面**，前端不硬编码任何选项
- 界面按模式自动显隐：图生/首尾帧显示图片槽并隐藏比例；万能参考显示 3 个参考图槽；
  文生图隐藏时长/分辨率/超分

只有当你要**新增字段**（比如加一个「模型选择」或「镜头运动」下拉）时才改前端，
此时需同步改三个地方：`web/index.html`（加 DOM）、`web/app.js`（加提交字段）、
`server.py` 的 `/api/submit`（接收字段）与 `store.create()`（落库）。

> 改前端前请先读 `references/API_AND_UI.md`，里面有完整界面结构图、
> **关键 DOM id 清单**（前端 JS 依赖这些 id，改名即失效）和提交参数字段表。

### 2.7 设导演令牌（给 API / CODEX 用）

管理员 PowerShell 执行一次（写入 Windows 用户环境变量，重启后 `run_server.py` 自动从注册表读）：

```powershell
[Environment]::SetEnvironmentVariable("H3_DIRECTOR_TOKEN", (New-Guid).ToString()+"x", "User")
```

记下输出值，给 CODEX / 远端脚本用。也可临时在 CMD 里 `set H3_DIRECTOR_TOKEN=xxx` 后立即启动。

### 2.8 启动并本地验证

```bat
.venv\Scripts\python.exe run_server.py
```

看到 `ComfyUI 手机控制台已启动` + `http://127.0.0.1:8790` 即成功。浏览器开 `http://127.0.0.1:8790`，输密码应能进控制台。手机连同一 WiFi，开 `http://<第二台电脑局域网IP>:8790` 也应能访问。

---

## 3. 配置固定隧道（comfy2.cnun.com 已就绪，直接复用）

第一台已在 Cloudflare 把 `cnun.com` 接入，并在其下建好子域 **`comfy2.cnun.com`**（DNS 路由 + tunnel `comfy2-console` 已创建，Tunnel ID `735569eb-0731-4e39-ae00-996d8e9bc375`）。第二台**无需再登录 Cloudflare、无需再 create / route dns**，直接拿凭证跑即可。

### 方案 A：直接复用 comfy2.cnun.com（推荐，最省事）

1. 从第一台拷贝这两样到第二台（U 盘 / 同步盘均可）：
   - **凭证**：`C:\Users\wzlimon\.cloudflared\735569eb-0731-4e39-ae00-996d8e9bc375.json`
     → 第二台放到 `C:\Users\<你的用户名>\.cloudflared\735569eb-0731-4e39-ae00-996d8e9bc375.json`
   - **配置模板**：本仓库 `references/cloudflared-config-comfy2.example.yml`
     → 第二台放到 `D:\comfy-mobile-studio\cloudflared-config-comfy2.yml`，并把里面的 `credentials-file` 改成你第二台的实际路径
2. 确保 2.8 的控制台已在 8790 跑着，启动隧道：
   ```bat
   tools\cloudflared.exe tunnel --config cloudflared-config-comfy2.yml run comfy2-console
   ```
3. 浏览器 / 手机流量开 `https://comfy2.cnun.com` → 应看到控制台登录页（HTTP 200）。

> ⚠️ **同一 tunnel 不要两台同时长期运行**：Cloudflare HA 会把 `comfy2.cnun.com` 流量在两台间随机分配。正式切到第二台前，先在第一台停掉 comfy2 隧道（让第一台 WorkBuddy 执行「停本机 comfy2 隧道」），第二台再启动。

### 方案 B：第二台自建独立隧道（不想共享第一台凭证时）

若你希望两台完全独立（各自一个 tunnel、互不干扰），走完整自建流程：

#### 3.1 域名接入 Cloudflare（只改 NS，不转移）

1. Cloudflare 后台「添加域名 / Add a Site」→ 填你的域名（可复用 `cnun.com` 再加子域如 `comfy3.cnun.com`，或在同账号下加第二个站点）
2. 它给你两条 NS（如 `xxx.ns.cloudflare.com` / `yyy.ns.cloudflare.com`）
3. 回**买域名的注册商后台**，把域名的 DNS 服务器改成这两条
   - ⚠️ 是「改 NS（连接/接入）」，**不是转移域名**（转移是换注册商，别点）
4. 等 NS 生效（几分钟~24 小时），Cloudflare 面板里该域名变「Active」

#### 3.2 本机装 cloudflared

```bat
winget install Cloudflare.cloudflared
```
或去 https://github.com/cloudflare/cloudflared/releases 下载 `cloudflared-windows-amd64.exe`，放到 `D:\comfy-mobile-studio\tools\cloudflared.exe`（放项目里可绕开 PATH 问题，和第一台一样）。

#### 3.3 登录 + 建隧道 + 绑子域（本机命令行）

```bat
cloudflared login
```
→ 浏览器弹出，勾选你的域名授权。

```bat
cloudflared tunnel create comfy-console
cloudflared tunnel route dns comfy-console comfy.你的域名.com
```
→ 自动在 Cloudflare DNS 里加好子域的 CNAME 指向隧道，不用手填。

记下 `create` 输出的 **Tunnel ID**（形如 `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`）。

#### 3.4 填配置 + 启动固定隧道

把 `cloudflared-config.example.yml` 复制为 `cloudflared-config.yml`，改三处：

```yaml
tunnel: comfy-console
credentials-file: C:\Users\<你的用户名>\.cloudflared\<TUNNEL_ID>.json
ingress:
  - hostname: comfy.你的域名.com
    service: http://localhost:8790
  - service: http_status:404
```

启动（先确保 2.8 的控制台已在跑）：

```bat
tools\cloudflared.exe tunnel --config cloudflared-config.yml run comfy-console
```

#### 3.5 验证

浏览器开 `https://comfy.你的域名.com` → 应看到控制台登录页（HTTP 200）。手机用流量（非 WiFi）访问同一个地址也应通。

> 临时方案（不绑域名）：`cloudflared tunnel --url http://localhost:8790` 会给你一个 `https://xxxx.trycloudflare.com` 临时地址，免费无流量限制，但每次重启地址都变。详见 `references/CLOUDFLARE.md`。

---

## 4. CODEX 自动化上传（可选，复用同一套）

第二台机器部署完后，CODEX / 远端脚本可用同一套批量上传接口：

```bat
.venv\Scripts\python.exe upload_assets.py --project 你的项目名 --subdir characters/model_sheets "图片1.png" "图片2.png"
```

- 认证：Bearer 头带 `H3_DIRECTOR_TOKEN`（Windows 用户环境变量，脚本自动读）
- 地址：本机 `http://127.0.0.1:8790`；若 CODEX 在远端，换第二台局域网 IP + 确保 8790 可达（或走 3 的固定域名）
- 提交时引用：`ref_0_name=项目名/subdir/文件.png`
- 完整说明见第一台机器 `D:\comfy-mobile-studio\CODEX_ASSETS.md`，逻辑完全一致

---

## 5. 常用命令 / 故障排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 手机打不开 `:8790` | 防火墙挡了 | 以管理员运行 `开放端口.bat`（见 launch_scripts.md），或手动放行 TCP 8790 入站 |
| 外网报 Cloudflare 1033 | 本机 `:8790` 服务掉了 / cloudflared 进程死了 | 先重启控制台（`run_server.py`），再重启隧道（`cloudflared ... run comfy-console`）。**务必用 .bat/任务计划程序启动，别用临时命令行**，否则进程会被回收 |
| 上传/提交报 401 | 没带 `H3_DIRECTOR_TOKEN` 或令牌错 | 确认 Windows 用户环境变量已设，或 run_server.py 启动时注入了 |
| `cloudflared` 不在 PATH | 用 `tools\cloudflared.exe` 绝对路径调用（见第一台做法） | 把 `tools\` 加进 PATH，或 bat 里写 `set "CF=%~dp0tools\cloudflared.exe"` |

**防止再断链（强烈建议）**：把「启动控制台 + 启动隧道」做成一个 `.bat`，并用 Windows 任务计划程序（`schtasks /create /tn ComfyConsole /tr "D:\comfy-mobile-studio\启动全部.bat" /sc onlogon /rl highest /f`）设为登录自启，使进程脱离任何会话独立存活。

---

## 目录结构（最终应长这样）

```
D:\comfy-mobile-studio\
├── server.py                     # 控制台（← 用 server_reference.py 改名，别用 template）
├── run_server.py                 # 启动引导：从注册表读令牌后拉起 server.py
├── config.yaml                   # 配置（由 config.example.yaml 改名）★ 唯一需手工改的
├── upload_assets.py              # CODEX 批量上传 CLI
├── inspect_workflow.py           # 查工作流节点 ID 的工具（适配必用）
├── sync_from_skill.bat           # git pull 后双击：同步最新版，保留 config.yaml
├── cloudflared-config.yml        # 固定隧道配置
├── autostart.bat                 # 一键拉起 server + 隧道（脱离会话常驻）
├── tools\cloudflared.exe         # 隧道客户端（可选，放项目里绕开 PATH）
├── core\                         # 后端模块（6 个，整目录拷）
├── web\
│   ├── index.html
│   ├── app.js
│   └── style.css                 # ★ 必须有，少了排版全乱
├── workflows\                    # 你的 ComfyUI 工作流 JSON（按 2.5 接入）
└── data\
    ├── tasks.db
    ├── uploads\                  # 资产库（项目专库）
    └── outputs\                  # 成品输出
```
