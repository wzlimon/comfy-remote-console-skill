# Windows 启动脚本

把下面三个 `.bat` 放到项目根目录（与 server.py 同级）。均用 GBK/UTF-8 兼容写法（`chcp 65001`）。

## 安装依赖.bat
```bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 安装依赖
if not exist ".venv\Scripts\python.exe" (
    echo 正在创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建失败，请确认已安装 Python 3.10 以上版本
        pause & exit /b 1
    )
)
echo 正在安装依赖...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install flask requests pyyaml waitress
echo.
echo 安装完成，接下来双击「启动.bat」即可。
pause
```

## 启动.bat（经 run_server.py 注入令牌后启动）
```bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ComfyUI 手机控制台
if not exist ".venv\Scripts\python.exe" (
    echo [错误] 没找到虚拟环境，请先双击运行「安装依赖.bat」
    pause & exit /b 1
)
echo 正在启动服务...
echo.
".venv\Scripts\python.exe" run_server.py
echo.
echo 服务已停止。
pause
```

## 开放端口.bat（让局域网手机能连，需右键「以管理员身份运行」）
```bat
@echo off
chcp 65001 >nul
title 开放防火墙端口 8790
net session >nul 2>&1
if errorlevel 1 (
    echo [需要管理员权限] 请右键这个文件，选择「以管理员身份运行」
    pause & exit /b 1
)
echo 正在添加防火墙入站规则，允许局域网访问 8790 端口...
netsh advfirewall firewall delete rule name="ComfyUI 手机控制台" >nul 2>&1
netsh advfirewall firewall add rule name="ComfyUI 手机控制台" dir=in action=allow protocol=TCP localport=8790 profile=private,domain
if errorlevel 1 (
    echo [失败] 请手动在 Windows 防火墙-高级设置-入站规则 里放行 TCP 8790
) else (
    echo 完成。现在手机应该能打开了。
)
pause
```

## 公网隧道.bat（外网访问，用 Cloudflare trycloudflare，免费无流量限制）
```bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ComfyUI 公网隧道 (trycloudflare)
where cloudflared >nul 2>&1
if errorlevel 1 (
    echo [提示] 未检测到 cloudflared，请先安装：
    echo   winget install Cloudflare.cloudflared
    echo   或下载 https://github.com/cloudflare/cloudflared/releases 的 cloudflared-windows-amd64.exe 放到本目录
    pause & exit /b 1
)
echo 正在建立 trycloudflare 免费临时隧道（无流量限制，重启地址即变）...
echo 请确保「启动.bat」已先把控制台拉起在 :8790。
echo 打开下方输出的 https://xxxx.trycloudflare.com 即可外网访问控制台。
echo.
cloudflared tunnel --url http://localhost:8790
pause
```
> 跑之前请先双击「启动.bat」把控制台拉起（:8790）。临时隧道每次重启地址都变，详见 `references/CLOUDFLARE.md`。

## 固定隧道.bat（固定域名，长期方案，用 Cloudflare 自有域名）
```bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ComfyUI 固定隧道 (Cloudflare Tunnel)
where cloudflared >nul 2>&1
if errorlevel 1 (
    echo [提示] 未检测到 cloudflared，请先 winget install Cloudflare.cloudflared
    pause & exit /b 1
)
if not exist "cloudflared-config.yml" (
    echo [错误] 找不到 cloudflared-config.yml，请先复制 cloudflared-config.example.yml 并填好 tunnel/credentials-file/hostname
    pause & exit /b 1
)
echo 正在拉起固定隧道（地址固定不变）...
cloudflared tunnel --config cloudflared-config.yml run comfy-console
pause
```
> 需先在 Cloudflare 后台接入域名，并 `cloudflared login` → `create` → `route dns`，详见 `references/CLOUDFLARE.md` 第五节。
```

## 设导演令牌（管理员 PowerShell，一次性）
```powershell
[Environment]::SetEnvironmentVariable("H3_DIRECTOR_TOKEN", (New-Guid).ToString()+"x", "User")
# 记下输出的令牌值，给 CODEX / 远端脚本用；重启后 run_server.py 会自动从注册表读
```
