# 外网访问：ngrok 公网隧道

目标：让不在同一 WiFi 的手机/远端也能访问控制台。

## 免费版（地址重启即变）
1. 下载 ngrok，解压得到 `ngrok.exe`。
2. 绑定账号拿 authtoken（一次性）：
   ```
   ngrok config add-authtoken <你的token>
   ```
3. 另开一个命令行窗口跑：
   ```
   ngrok http 8790
   ```
4. 终端会打印一个 `https://xxxx.ngrok-free.app` 地址，发给手机即可访问。

⚠️ 免费版每次重启 ngrok 地址都变，需重新发链接。

## 固定域名（付费版）
```
ngrok http --domain=your-name.ngrok.dev 8790
```
之后地址固定。

## 更稳的替代
- **Cloudflare Tunnel**：免费、固定地址、无需开放防火墙端口，推荐长期使用。
  ```
  cloudflared tunnel --url http://localhost:8790
  ```
- **frp / 路由器端口转发**：有公网 IP 或内网穿透服务器时自建。

## 安全提醒（开外网前必看）
- 务必在 `config.yaml` 设强 `server.password`（外网穿透必须）。
- 固定隧道地址（如 Cloudflare / 付费 ngrok）会长期暴露，建议配合强密码 + 限制可访问 IP。
- 免费 ngrok 流量会被 ngrok 服务端看到，敏感素材慎用。
