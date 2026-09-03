@echo off
REM ============================================================
REM  sync_from_skill.bat
REM  把 SKILL 仓库(git pull 后的最新版)同步到控制台项目目录
REM  放到 D:\comfy-mobile-studio\ 下双击运行
REM  纯 ASCII，避免中文乱码
REM ============================================================
setlocal

set "SKILL=%USERPROFILE%\.workbuddy\skills\comfy-remote-console\references"
set "DST=%~dp0"

echo === sync_from_skill ===
echo SRC: %SKILL%
echo DST: %DST%
echo.

if not exist "%SKILL%" (
  echo [ERROR] SKILL dir not found.
  echo Clone it first:
  echo   git clone https://github.com/wzlimon/comfy-remote-console-skill.git "%%USERPROFILE%%\.workbuddy\skills\comfy-remote-console"
  goto :end
)

echo --- core/ ---
xcopy "%SKILL%\core\*.py" "%DST%core\" /Y /I /Q

echo --- workflows/ ---
xcopy "%SKILL%\workflows\*.json" "%DST%workflows\" /Y /I /Q

echo --- web/ ---
xcopy "%SKILL%\web\*" "%DST%web\" /Y /I /Q

echo --- server.py ---
copy /Y "%SKILL%\server_reference.py" "%DST%server.py"

echo --- scripts ---
copy /Y "%SKILL%\run_server.py" "%DST%" 
copy /Y "%SKILL%\upload_assets.py" "%DST%"
copy /Y "%SKILL%\inspect_workflow.py" "%DST%"

echo --- config ---
if exist "%DST%config.yaml" (
  echo KEEP your config.yaml - not overwritten
  copy /Y "%SKILL%\config.example.yaml" "%DST%config.example.yaml"
) else (
  copy /Y "%SKILL%\config.example.yaml" "%DST%config.yaml"
  echo config.yaml created from example - EDIT IT
)

echo --- cloudflared config ---
if not exist "%DST%cloudflared-config.yml" (
  copy /Y "%SKILL%\cloudflared-config.example.yml" "%DST%cloudflared-config.yml"
)

echo.
echo === done ===
echo Now restart the console:
echo   .venv\Scripts\python.exe run_server.py

:end
echo.
pause
