@echo off
title Restore do Lab Kubernetes
setlocal EnableDelayedExpansion

set "BACKUPROOT=F:\Backups\k8s-lab-vmware"

if not exist "%BACKUPROOT%" (
  echo Pasta de backup nao encontrada: %BACKUPROOT%
  pause
  exit /b 1
)

echo.
echo Backups disponiveis:
echo.

set /a i=0
for /f "delims=" %%D in ('dir /b /ad /o-n "%BACKUPROOT%"') do (
  set /a i+=1
  set "BKP[!i!]=%%D"
  echo !i!^) %%D
)

if %i%==0 (
  echo Nenhum backup encontrado em %BACKUPROOT%
  pause
  exit /b 1
)

echo.
set /p CHOICE="Digite o numero do backup que deseja restaurar: "

if not defined BKP[%CHOICE%] (
  echo Opcao invalida.
  pause
  exit /b 1
)

set "SELECTED=%BACKUPROOT%\!BKP[%CHOICE%]!"
echo.
echo Backup selecionado: !SELECTED!
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Restore-k8sLab.ps1" -BackupFolder "!SELECTED!"

echo.
echo Restore finalizado.
pause
