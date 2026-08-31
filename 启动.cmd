@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 没找到运行环境，先跑一次 tools\setup_env.py
  pause
  exit /b 1
)

if not exist "app\node_modules\electron" (
  echo 正在安装界面组件，第一次会慢一点...
  pushd app
  call npm install --no-audit --no-fund
  popd
)

pushd app
call npx electron .
popd
