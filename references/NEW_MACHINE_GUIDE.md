# 另一台电脑部署教程：手机远程 ComfyUI 控制台

> 适用场景：你在第二台装了 ComfyUI 的 Windows 电脑上，想复刻「手机网页远程操控 + 项目专库 + Cloudflare 固定域名隧道」这套能力。
> 本教程配合 `comfy-remote-console` SKILL 使用。你在本机（第一台）已验证过的全部关键模式（双认证、防穿越、项目专库、批量上传、退出登录、固定隧道）都在这套代码里。

---

## 0. 前置条件（第二台电脑）

- Windows 10/11，已装 **Python 3.10+**（建议 3.13）
- 本机 **ComfyUI 已在运行**，且能访问 `http://127.0.0.1:8188`
- 有 Git（用于克隆 SKILL）或直接能下载 ZIP
- 一个 **新域名**（准备在 Cloudflare 接入；也可用同一 Cloudflare 账号下加第二个站点）
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

从克隆/解压得到的 skill 目录，复制以下文件到项目根：

```
comfy-remote-console-skill\references\server_template.py   ->  D:\comfy-mobile-studio\server.py
comfy-remote-console-skill\references\run_server.py        ->  D:\comfy-mobile-studio\run_server.py
comfy-remote-console-skill\references\config.example.yaml  ->  D:\comfy-mobile-studio\config.yaml
comfy-remote-console-skill\references\upload_assets.py     ->  D:\comfy-mobile-studio\upload_assets.py
comfy-remote-console-skill\references\web\                 ->  D:\comfy-mobile-studio\web\
comfy-remote-console-skill\references\cloudflared-config.example.yml -> D:\comfy-mobile-studio\cloudflared-config.example.yml
```

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

### 2.5 接后端：把 `submit_to_comfyui()` 指向你的 ComfyUI 工作流 ★关键一步

`server.py` 里的 `submit_to_comfyui()` 现在是占位函数。你需要把它改成：读前端表单字段 → 拼成你 ComfyUI 工作流的 API 格式（`/prompt` 接口需要的 JSON）→ `POST http://127.0.0.1:8188/prompt`。

参考你的第一台机器 `D:\comfy-mobile-studio\core\comfy.py` / `core\pipeline.py` 的写法（把工作流 JSON 放到 `workflows/` 目录，用 `requests.post` 投到 8188）。这一步每台机器的模型/节点不同，**必须按你这台的工作流改**，没法通用。

> 想省事：把第一台机器 `D:\comfy-mobile-studio\` 整个目录直接拷到第二台同名位置，然后只改 `config.yaml` 的 `comfyui` 地址/密码即可获得完整功能（含项目专库、上传 API、退出登录按钮）。SKILL 模板是精简框架，生产目录功能更全。

### 2.6 （可选）改前端表单 `web/app.js`

如果第一台机器的表单字段和这台不一样，按你工作流需要的字段调整 `web/index.html` 和 `web/app.js`（例如分辨率、时长、模型选择等）。SKILL 模板给的是最小可用表单。

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

## 3. 配置新域名的 Cloudflare 固定隧道

第二台机器用**新域名**（假设为 `newdomain.com`，子域用 `comfy.newdomain.com`）。

### 3.1 域名接入 Cloudflare（只改 NS，不转移）

1. Cloudflare 后台「添加域名 / Add a Site」→ 填 `newdomain.com`
2. 它给你两条 NS（如 `xxx.ns.cloudflare.com` / `yyy.ns.cloudflare.com`）
3. 回你**买域名的注册商后台**，把域名的 DNS 服务器改成这两条
   - ⚠️ 是「改 NS（连接/接入）」，**不是转移域名**（转移是换注册商，别点）
4. 等 NS 生效（几分钟~24 小时），Cloudflare 面板里该域名变「Active」

### 3.2 本机装 cloudflared

```bat
winget install Cloudflare.cloudflared
```
或去 https://github.com/cloudflare/cloudflared/releases 下载 `cloudflared-windows-amd64.exe`，放到 `D:\comfy-mobile-studio\tools\cloudflared.exe`（放项目里可绕开 PATH 问题，和第一台一样）。

### 3.3 登录 + 建隧道 + 绑子域（本机命令行）

```bat
cloudflared login
```
→ 浏览器弹出，**勾选 `newdomain.com` 授权**。

```bat
cloudflared tunnel create comfy-console
cloudflared tunnel route dns comfy-console comfy.newdomain.com
```
→ 自动在 Cloudflare DNS 里加好 `comfy.newdomain.com` 的 CNAME 指向隧道，不用手填。

记下 `create` 输出的 **Tunnel ID**（形如 `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`）。

### 3.4 填配置 + 启动固定隧道

把 `cloudflared-config.example.yml` 复制为 `cloudflared-config.yml`，改三处：

```yaml
tunnel: comfy-console
credentials-file: C:\Users\<你的用户名>\.cloudflared\<TUNNEL_ID>.json
ingress:
  - hostname: comfy.newdomain.com
    service: http://localhost:8790
  - service: http_status:404
```

启动（先确保 2.8 的控制台已在跑）：

```bat
tools\cloudflared.exe tunnel --config cloudflared-config.yml run comfy-console
```

### 3.5 验证

浏览器开 `https://comfy.newdomain.com` → 应看到控制台登录页（HTTP 200）。手机用流量（非 WiFi）访问同一个地址也应通。

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
├── server.py                     # 控制台（由 server_template.py 改名）
├── run_server.py                 # 启动引导：从注册表读令牌后拉起 server.py
├── config.yaml                   # 配置（由 config.example.yaml 改名）
├── upload_assets.py              # CODEX 批量上传 CLI
├── cloudflared-config.yml        # 固定隧道配置
├── tools\cloudflared.exe         # 隧道客户端（可选，放项目里绕开 PATH）
├── web\
│   ├── index.html
│   └── app.js
├── workflows\                    # 你的 ComfyUI 工作流 JSON（按 2.5 接入）
└── data\
    ├── tasks.db
    ├── uploads\                  # 资产库（项目专库）
    └── outputs\                  # 成品输出
```
