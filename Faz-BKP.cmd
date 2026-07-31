@echo off
title Backup do Lab Kubernetes
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Backup-K8sLab.ps1" -RestartAfterBackup
echo.
echo Backup finalizado.
pause
