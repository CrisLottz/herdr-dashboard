@echo off
rem Launcher: uses the repo's venv (created by `herdr plugin install` or by hand).
"%~dp0.venv\Scripts\python.exe" "%~dp0pcmon.py" %*
