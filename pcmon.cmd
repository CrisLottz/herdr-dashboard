@echo off
rem Lanzador: usa el venv del repo (creado por `herdr plugin install` o a mano).
"%~dp0.venv\Scripts\python.exe" "%~dp0pcmon.py" %*
