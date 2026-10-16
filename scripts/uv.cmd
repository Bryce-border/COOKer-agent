@echo off
rem Project-local uv wrapper for cmd / PowerShell
set "ROOT=%~dp0.."
set "UV_PYTHON_INSTALL_DIR=%ROOT%\tools\python"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_PYTHON_BIN_DIR=%ROOT%\tools\python\bin"
set "UV_PYTHON_INSTALL_BIN=0"
"%ROOT%\tools\uv\uv.exe" %*
