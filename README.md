# comfy-remote-console

把本机 **ComfyUI（8188 端口）** 包装成一个**手机浏览器就能远程操控的网页控制台**：表单填提示词 → 队列提交 → 本机生成 → 网页取片/下载。

适用于：想让另一台装了 ComfyUI 的 Windows 电脑也具备手机远程能力，或给 CODEX / 自动化脚本开放批量上传资产接口。

## 核心能力

- **双认证**：网页密码（cookie session，持久化）＋ 导演令牌 `H3_DIRECTOR_TOKEN`（Bearer，供 CODEX / 自动化调度，无 cookie 也能调 API）
- **项目专库资产管理**：`uploads/<project>/<subdir>/`；`/api/refs` 递归扫描、`/api/upload` 安全批量上传（同名覆盖）、`/api/submit` 支持相对路径引用
- **安全边界**：`safe_upload_path` 防 `..`/绝对路径/盘符/非白名单后缀穿越；令牌只存 Windows 用户环境变量，绝不进配置文件
- **外网访问**：ngrok / Cloudflare Tunnel 指南（见 `references/NGROK.md`）

## 目录结构

```
SKILL.md                     使用说明（架构 / 部署步骤 / 安全清单）
references/
  server_template.py         自包含最小控制台（已实跑验证，改后端即可用）
  run_server.py              从注册表读 H3_DIRECTOR_TOKEN 后启动
  config.example.yaml        配置样例
  upload_assets.py           CODEX 批量上传 CLI（纯标准库）
  launch_scripts.md          安装依赖/启动/开放防火墙 的 .bat
  web/                       最小手机前端模板（index.html + app.js）
  CLOUDFLARE.md               外网穿透（trycloudflare / 固定域名）指南
  cloudflared-config.example.yml  固定隧道配置模板
```

## 在 WorkBuddy 里使用

1. 安装本 skill（见下方「安装」）。
2. 让 WorkBuddy 加载 `comfy-remote-console`，按 `SKILL.md` 把 `server_template.py` 的
   `submit_to_comfyui()` 接到你自己的 ComfyUI 工作流。
3. 设 `H3_DIRECTOR_TOKEN`、启动、手机访问。

## 安装

**方式 A：Git 克隆到用户级 skills 目录（推荐，可更新）**

```bash
git clone https://github.com/wzlimon/comfy-remote-console-skill.git \
  "$HOME/.workbuddy/skills/comfy-remote-console"
```

**方式 B：下载 ZIP 解压到**

```
C:\Users\<用户名>\.workbuddy\skills\comfy-remote-console\
```

放好后重启 WorkBuddy，即可在可用 skills 中看到 `comfy-remote-console`。

## 安全提醒（开外网前必看）

- `config.yaml` 的 `server.password` 务必设强密码。
- `H3_DIRECTOR_TOKEN` 只放 Windows 用户环境变量（HKCU\Environment），勿入库 / 勿写配置文件。
- 免费 ngrok 地址重启即变且流量经三方，敏感素材慎用；长期使用建议 Cloudflare Tunnel 固定地址。
