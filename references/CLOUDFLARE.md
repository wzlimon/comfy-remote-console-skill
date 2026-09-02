# 外网访问：Cloudflare trycloudflare 临时隧道

目标：让不在同一 WiFi / 异地的手机、CODEX、远端脚本也能访问控制台，且**无流量限制**。

> 为什么从 ngrok 换到 trycloudflare：ngrok 免费版有**每月流量上限**，超了就断；trycloudflare（Cloudflare quick tunnel）**完全免费、无需注册、无流量限制**，更适合长期挂着跑 AI 生成任务。

## 一、为什么用 trycloudflare（quick tunnel）
- ✅ 完全免费、无需注册账号、无需 authtoken
- ✅ **无流量限制**（ngrok 免费版每月有流量上限，超了就断）
- ✅ 不用开放防火墙端口、不用做路由器端口转发（Cloudflare 反向代理进你本机）
- ⚠️ 每次重启 `cloudflared` 地址都会变（和 ngrok 免费版一样），需重新把链接发给手机

## 二、安装 cloudflared（任选一种）
- **winget**（推荐）：`winget install Cloudflare.cloudflared`
- **GitHub Release**：https://github.com/cloudflare/cloudflared/releases → 取 `cloudflared-windows-amd64.exe`，放到项目目录或加入 PATH
- **官方下载页**：https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

验证：`cloudflared --version`

> 国内下载 GitHub Release 可能偏慢，可优先用 winget 或官方下载页的镜像。

## 三、建立临时隧道
确保控制台已在 `http://localhost:8790` 跑起来（双击「启动.bat」），然后**另开一个命令行窗口**进项目目录跑：
```
cloudflared tunnel --url http://localhost:8790
```
启动后终端会打印类似：
```
Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):
 https://xxxx.trycloudflare.com
```
把这个 `https://xxxx.trycloudflare.com` 发给手机即可外网访问控制台。

## 四、给 CODEX / 自动化脚本取临时 URL
临时隧道的地址只打印在 stdout，脚本可用 `--logfile` 落地后解析：
```
cloudflared tunnel --url http://localhost:8790 --logfile cloudflared.log
```
然后从 `cloudflared.log` 里 grep `trycloudflare.com` 取出地址；或用 PowerShell 实时读 stdout 捕获。

## 五、固定域名（长期方案，可选）
若想地址固定（重启不变），需用 Cloudflare 账号 + 自有域名：
```
cloudflared login
cloudflared tunnel create comfy-console
cloudflared tunnel route dns comfy-console comfy.example.com
cloudflared tunnel run --url http://localhost:8790 comfy-console
```
之后 `https://comfy.example.com` 固定不变，适合把地址长期写进 CODEX 配置。

## 六、安全提醒（开外网前必看）
- 务必在 `config.yaml` 设强 `server.password`（外网穿透**必须**）。
- trycloudflare 地址随机、且流量会经 Cloudflare 中继；敏感素材请自担风险，或改用固定隧道 + 强密码 + 限制来源 IP。
- 临时隧道每次重启换地址，不要把旧链接长期写死在脚本里；用「运行时从日志取地址」更稳。
