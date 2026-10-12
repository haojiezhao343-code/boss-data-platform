@echo off
title 河南鼎誉智审平台 - 停止
echo 停止平台服务（端口 17954）...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":17954" ^| findstr "LISTENING"') do taskkill /PID %%p /F

echo 停止基础设施容器 ...
cd /d F:\boss-data-platform
docker compose stop milvus etcd mongodb minio
cd /d F:\data_agent\docker
docker compose -p data_agent stop

echo.
echo 已全部停止。重新启动请运行：启动平台.bat
pause