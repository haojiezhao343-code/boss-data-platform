@echo off
setlocal enabledelayedexpansion
title 河南鼎誉智审平台 - 一键启动
echo ================================================
echo     河南鼎誉智审平台  一键启动
echo ================================================
echo.

echo [1/4] 检查 Docker Desktop ...
docker info >nul 2>&1
if not errorlevel 1 goto dockerok
echo   Docker 未运行，正在启动 Docker Desktop（约 1-2 分钟）...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
set /a tries=0
:waitdocker
timeout /t 5 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto dockerok
set /a tries+=1
if !tries! lss 36 goto waitdocker
echo   [错误] Docker 启动超时，请手动启动 Docker Desktop 后重新运行本脚本
pause
exit /b 1
:dockerok
echo   Docker 已就绪
echo.

echo [2/4] 启动基础设施容器（MySQL / Qdrant / ES / Embedding / Milvus / MongoDB / MinIO）...
cd /d F:\data_agent\docker
docker start data-agent-mysql qdrant elasticsearch embedding kibana >nul 2>&1
docker start kb-milvus kb-etcd kb-mongodb kb-minio >nul 2>&1
docker compose -p data_agent up -d >nul 2>&1
cd /d F:\boss-data-platform
docker compose up -d mongodb minio etcd milvus >nul 2>&1
echo.

echo [3/4] 等待基础服务就绪（最长约 2 分钟）...
set /a n=0
:waitsvc
timeout /t 5 /nobreak >nul
set /a ok=0
curl -s -o nul --noproxy "*" http://127.0.0.1:6333 && set /a ok+=1
curl -s -o nul --noproxy "*" http://127.0.0.1:19200 && set /a ok+=1
curl -s -o nul --noproxy "*" http://127.0.0.1:9091/healthz && set /a ok+=1
if !ok! lss 3 (
    set /a n+=1
    if !n! lss 24 goto waitsvc
    echo   [警告] 部分基础服务未就绪（Qdrant/ES/Milvus），仍尝试继续启动...
    goto svcdone
)
echo   Qdrant / Elasticsearch / Milvus 均已就绪
:svcdone
echo.

echo [4/4] 启动平台服务（新窗口运行，请勿关闭该窗口）...
start "鼎誉智审平台服务" cmd /k "cd /d F:\boss-data-platform && set PYTHONPATH=F:/knowledge_base/.venv/Lib/site-packages;F:/data_agent/.venv/Lib/site-packages;. && F:\knowledge_base\.venv\Scripts\python.exe -u main.py"

set /a m=0
:waithc
timeout /t 5 /nobreak >nul
curl -s -o nul --noproxy "*" http://127.0.0.1:17954/health
if not errorlevel 1 goto hcover
set /a m+=1
if !m! lss 24 goto waithc
echo   [警告] 平台服务启动较慢，请稍后手动访问 http://127.0.0.1:17954/
goto done
:hcover
echo   平台服务已就绪，正在打开浏览器 ...
start "" http://127.0.0.1:17954/
:done
echo.
echo ================================================
echo   启动完成！访问地址：http://127.0.0.1:17954/
echo   停止平台请运行：停止平台.bat
echo ================================================
pause