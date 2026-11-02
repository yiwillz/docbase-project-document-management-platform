@echo off
setlocal
title docbase installation
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "DEPLOY_EXIT_CODE=%ERRORLEVEL%"
echo.
if "%DEPLOY_EXIT_CODE%"=="0" (
 echo Installation completed. Default address: https://localhost
 echo Default credentials file: C:\docbase\config\setup-credentials.json
) else (
  echo Installation failed. Review the messages above and the installation log.
  echo Default log: C:\docbase\logs\bootstrap.log
)
pause
exit /b %DEPLOY_EXIT_CODE%
