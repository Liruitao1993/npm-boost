@echo off
chcp 65001 >nul
title NPM 加速器
"D:\Program Files\Python313\python.exe" "%~dp0main.py"
if errorlevel 1 pause
