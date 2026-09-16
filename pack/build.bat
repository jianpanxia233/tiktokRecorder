@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

echo ============================================
echo   DouyinLiveRecorder  Windows 打包
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 python。请先安装 Python 3.10+，安装时勾选 "Add Python to PATH"
    exit /b 1
)

rem 优先复用已有的 .venv，没有就用独立的 .venv-build，避免污染开发环境
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
    echo 使用已有虚拟环境：.venv
) else if exist ".venv-build\Scripts\python.exe" (
    set "PY=.venv-build\Scripts\python.exe"
    echo 使用已有虚拟环境：.venv-build
) else (
    echo 创建虚拟环境 .venv-build ...
    python -m venv .venv-build
    if errorlevel 1 exit /b 1
    set "PY=.venv-build\Scripts\python.exe"
)

if not exist "app\secret.py" (
    echo.
    echo [错误] 缺少 app\secret.py，卡密密钥还没生成。
    echo        先运行:  %PY% tools\gen_secret.py
    echo        （注意：这个密钥必须和你发卡时用的是同一个，否则发出的卡验不过）
    exit /b 1
)

echo.
echo [1/5] 安装依赖 ...
%PY% -m pip install -q --upgrade pip
if errorlevel 1 exit /b 1
%PY% -m pip install -q -r requirements-app.txt pyinstaller
if errorlevel 1 exit /b 1

echo [2/5] 清理旧产物 ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/5] PyInstaller 打包（几分钟）...
%PY% -m PyInstaller pack\DouyinLiveRecorder.spec --noconfirm
if errorlevel 1 exit /b 1

echo [4/5] 后处理：配置模板 / ffmpeg / node（首次要下载约 60MB）...
%PY% pack\post_build.py --app-dir dist\DouyinLiveRecorder --ffmpeg --node
if errorlevel 1 exit /b 1

echo [5/5] 压缩 ...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\DouyinLiveRecorder\*' -DestinationPath 'dist\DouyinLiveRecorder-windows.zip' -Force"
if errorlevel 1 exit /b 1

echo.
echo ============================================
echo   完成
echo   exe: dist\DouyinLiveRecorder\DouyinLiveRecorder.exe
echo   zip: dist\DouyinLiveRecorder-windows.zip
echo ============================================
endlocal
