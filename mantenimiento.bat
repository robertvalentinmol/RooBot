@echo off
REM RooBot - mantenimiento. Doble clic y elige una opcion.
REM Para opciones avanzadas sigue valiendo:  python -m vgclab.calibrar --help  (y lo mismo con importar, experimentos, experiencia, sprites)
cd /d "%~dp0"
title RooBot - mantenimiento

:menu
cls
echo.
echo   ====================================================
echo     RooBot  -  mantenimiento
echo   ====================================================
echo.
echo     1   Rutina mensual completa                (~1-1,5 h, internet)
echo         equipos nuevos + sprites + juez actualizado + comprobacion
echo.
echo     2   Importar equipos nuevos y sus sprites   (5-10 min, internet)
echo     3   Actualizar el juez con repeticiones nuevas (15-25 min, internet)
echo     4   Comprobar el juez frente al de siempre  (25-35 min)
echo     5   Medir la app (calibracion rapida)       (~12 min)
echo.
echo     6   Experimentos, de noche                  (3-4 h)
echo     7   Experiencia                             (1-1,5 h)
echo     8   Crear acceso directo en el escritorio
echo.
echo     0   Salir
echo.
echo   Cierra la app antes: estos procesos usan todo el procesador.
echo.
choice /c 123456780 /n /m "  Elige una opcion: "
if errorlevel 9 goto :eof
if errorlevel 8 goto acceso
if errorlevel 7 goto experiencia
if errorlevel 6 goto experimentos
if errorlevel 5 goto medir
if errorlevel 4 goto comprobar
if errorlevel 3 goto juez
if errorlevel 2 goto importar
if errorlevel 1 goto rutina
goto menu

:rutina
echo.
echo [1/5] Equipos nuevos...
call :hacer_importar
echo.
echo [2/5] Descargando repeticiones nuevas y actualizando el juez...
python -m vgclab.calibrar --aprender --replays 500 --rating 1500
echo.
echo [3/5] Midiendo con el juez de siempre (referencia)...
python -m vgclab.calibrar --juez manual
echo.
echo [4/5] Midiendo con el juez actual...
python -m vgclab.calibrar
echo.
echo [5/5] Prueba con tu revision de la app...
python -m vgclab.calibrar --prueba-revision
echo.
echo Rutina terminada. Abre la app: Mi progreso ^> Estado de la app.
goto fin

:importar
call :hacer_importar
goto fin

:juez
python -m vgclab.calibrar --aprender --replays 500 --rating 1500
goto fin

:comprobar
echo [1/3] Ensenando al juez con las repeticiones guardadas...
python -m vgclab.calibrar --aprender --solo-guardadas
echo.
echo [2/3] Midiendo con el juez de siempre (referencia)...
python -m vgclab.calibrar --juez manual
echo.
echo [3/3] Midiendo con el juez aprendido...
python -m vgclab.calibrar
echo.
echo Y la prueba con tu revision de la app...
python -m vgclab.calibrar --prueba-revision
goto fin

:medir
python -m vgclab.calibrar
goto fin

:experimentos
python -m vgclab.experimentos
goto fin

:experiencia
python -m vgclab.experiencia
goto fin

:acceso
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'RooBot.lnk')); $s.TargetPath=(Join-Path (Get-Location) 'app.bat'); $s.WorkingDirectory=(Get-Location).Path; $s.IconLocation=(Join-Path (Get-Location) 'web\img\roobot.ico'); $s.Description='RooBot - entrenamiento VGC'; $s.WindowStyle=7; $s.Save(); Write-Host ('Acceso directo creado en ' + $d)"
goto fin

:hacer_importar
if exist equipos_nuevos.txt goto hay_archivo
echo # Pega aqui tus equipos y guarda el archivo. Las lineas que empiezan por # se ignoran.> equipos_nuevos.txt
echo # Vale: enlaces de pokepast.es, uno por linea, o equipos exportados de Showdown.>> equipos_nuevos.txt
echo # Para varios equipos de Showdown, pon antes de cada uno una linea:  === Nombre ===>> equipos_nuevos.txt
echo He creado equipos_nuevos.txt y lo abro. Pega tus equipos, guardalo y cierralo para seguir.
start "" /wait notepad equipos_nuevos.txt
:hay_archivo
python -m vgclab.importar equipos_nuevos.txt
echo.
echo Sprites de los equipos (solo baja los que faltan)...
python -m vgclab.sprites
exit /b 0

:fin
echo.
pause
goto menu
