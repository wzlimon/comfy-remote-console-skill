# 外网访问：Cloudflare 隧道（trycloudflare 临时 / 自有域名固定）

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

## 三、建立临时隧道（最简单，地址重启即变）
确保控制台已在 `http://localhost:8790` 跑起来（双击「启动.bat」），然后**另开一个命令行窗口**进项目目录跑：
```
cloudflared tunnel --url http://localhost:8790
```
启动后终端会打印类似：
```
Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):
 https://xxxx.trycloudflare.com
```
把这个 `https://xxxx.trycloudflare.com` 发给手机即可外网访问控制台。本仓库自带「公网隧道.bat」一键起。

## 四、给 CODEX / 自动化脚本取临时 URL
临时隧道的地址只打印在 stdout，脚本可用 `--logfile` 落地后解析：
```
cloudflared tunnel --url http://localhost:8790 --logfile cloudflared.log
```
然后从 `cloudflared.log` 里 grep `trycloudflare.com` 取出地址；或用 PowerShell 实时读 stdout 捕获。

## 五、固定域名（长期方案，推荐长期使用）
适合把地址长期写进 CODEX 配置、手机书签、团队共享。需要：**Cloudflare 账号 + 一个已接入 Cloudflare 的域名**（在域名注册商后台把 NS 改成 Cloudflare 提供的）。

### 5.1 一次性准备（Cloudflare 后台 + 本机）
1. 注册 Cloudflare 账号，把你的域名 NS 改成 Cloudflare 提供的 nameserver。
2. 本机登录授权（浏览器会弹窗，选你的域名）：
   ```
   cloudflared login
   ```
   成功后凭证存到 `%USERPROFILE%\.cloudflared\cert.pem`。
3. 创建隧道（名字随意，如 `comfy-console`）：
   ```
   cloudflared tunnel create comfy-console
   ```
   记下输出的 **Tunnel ID**；同时 `%USERPROFILE%\.cloudflared\<tunnel-id>.json` 生成凭证文件。
4. 把子域名指向隧道（自动建 DNS 记录）：
   ```
   cloudflared tunnel route dns comfy-console comfy.你的域名.com
   ```

### 5.2 用配置文件持久运行（推荐）
把本项目 `references/cloudflared-config.example.yml` 复制为 `cloudflared-config.yml`，改好三处（见文件内注释），然后：
```
cloudflared tunnel --config cloudflared-config.yml run comfy-console
```
之后 `https://comfy.你的域名.com` **固定不变**，重启 `cloudflared` 也不变。本仓库自带「固定隧道.bat」一键起。

`cloudflared-config.example.yml` 内容：
```yaml
# 复制为 cloudflared-config.yml 后改三处：
# 1. tunnel:            与 `cloudflared tunnel create <名字>` 一致
# 2. credentials-file:  create 后生成的 <tunnel-id>.json 完整路径
# 3. hostname:          你的子域名（需先 `cloudflared tunnel route dns` 建好 DNS）
tunnel: comfy-console
credentials-file: C:\Users\你的用户名\.cloudflared\00000000-0000-0000-0000-000000000000.json
ingress:
  - hostname: comfy.你的域名.com
    service: http://localhost:8790
  - service: http_status:404
```

### 5.3 开机自启 / 常驻
把上面的 `cloudflared tunnel --config ... run comfy-console` 放进「任务计划程序」（触发器=登录时），或随「启动.bat」一起拉起，即可开机自动建立固定隧道。

> 固定域名长期暴露，务必 `server.password` 设强密码，且不要随便把子域名泄露到公网。

## 六、安全提醒（开外网前必看）
- 务必在 `config.yaml` 设强 `server.password`（外网穿透**必须**）。
- trycloudflare 地址随机、且流量会经 Cloudflare 中继；敏感素材请自担风险，或改用固定隧道 + 强密码 + 限制来源 IP。
- 临时隧道每次重启换地址，不要把旧链接长期写死在脚本里；用「运行时从日志取地址」更稳。
- 固定隧道地址固定，更要管好强密码与子域名泄露面。
