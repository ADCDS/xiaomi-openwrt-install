@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
pushd "%~dp0"
py -3 "%~dp0install.py" %*
set "INSTALL_EXIT=%ERRORLEVEL%"
popd
exit /b %INSTALL_EXIT%
