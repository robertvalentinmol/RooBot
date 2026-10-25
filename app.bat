@echo off
REM Ejercicios de posicion con interfaz grafica (se abre en tu navegador).
REM Ctrl+C para cerrar.
cd /d %~dp0
python -m vgclab.app %*
