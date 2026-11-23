@echo off
REM RooBot - mantenimiento. Doble clic y elige una opcion.
REM Para opciones avanzadas sigue valiendo:  python -m vgclab.calibrar --help  (y lo mismo con importar, experimentos, experiencia, sprites)
cd /d "%~dp0"
title RooBot - mantenimiento

:menu
cls
set "REGACTUAL=?"
for /f "delims=" %%r in ('python -m vgclab.regulacion --valor nombre 2^>nul') do set "REGACTUAL=%%r"
echo.
echo   ====================================================
echo     RooBot  -  mantenimiento        [%REGACTUAL%]
echo   ====================================================
echo.
echo     1   Rutina mensual completa                (~1-1,5 h, internet)
echo         equipos nuevos + sprites + juez actualizado + comprobacion
echo.
echo     2   Importar equipos nuevos y sus sprites   (5-10 min, internet)
echo         de la hoja de VGCPastes de la regulacion (y de equipos_nuevos.txt)
echo     3   Actualizar el juez con repeticiones nuevas (15-25 min, internet)
echo     4   Comprobar el juez frente al de siempre  (25-35 min)
echo     5   Medir la app (calibracion rapida)       (~12 min)
echo.
echo     6   Experimentos, de noche                  (3-4 h)
echo     7   Experiencia                             (1-1,5 h)
echo     8   Crear acceso directo en el escritorio
echo.
echo     9   Banco limpio: crear y medir             (~40 min)
echo         partidas nuevas para "Revisar la app" y la cifra de examen
echo.
echo     R   Nueva regulacion (M-D, M-E...)        (10-15 min, internet)
echo         actualiza Showdown, cambia de formato y pide los equipos nuevos
echo.
echo     G   Subir los cambios a GitHub (para la Thor) (1 min, internet)
echo         guarda en git equipos, juez, experiencia y regulacion, y los sube
echo.
echo     0   Salir
echo.
echo   Cierra la app antes: estos procesos usan todo el procesador.
echo.
choice /c 1234567890RG /n /m "  Elige una opcion: "
if errorlevel 12 goto subir
if errorlevel 11 goto nueva
if errorlevel 10 goto :eof
if errorlevel 9 goto limpio
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

:limpio
echo.
echo La primera vez elige las 100 partidas del banco limpio; despues siempre mide las mismas.
python -m vgclab.calibrar --banco-limpio
echo.
echo Hecho. Abre la app: "Revisar la app" te ira ensenando los desacuerdos de ese banco.
goto fin

:acceso
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'RooBot.lnk')); $s.TargetPath=(Join-Path (Get-Location) 'app.bat'); $s.WorkingDirectory=(Get-Location).Path; $s.IconLocation=(Join-Path (Get-Location) 'web\img\roobot.ico'); $s.Description='RooBot - entrenamiento VGC'; $s.WindowStyle=7; $s.Save(); Write-Host ('Acceso directo creado en ' + $d)"
goto fin

:nueva
echo.
echo   Nueva regulacion de Pokemon Champions
echo   -------------------------------------
echo   Actualiza Showdown, registra la regulacion y prepara su carpeta de equipos.
echo   La actual (%REGACTUAL%) no se borra: su juez y su experiencia se guardan en logs\regulaciones
echo   y puedes volver a ella con:  python -m vgclab.regulacion --usar ^<id^>
echo.
echo [1/4] Actualizando Pokemon Showdown...
pushd server\pokemon-showdown
git fetch -q origin master || (popd & echo [ERROR] No pude descargar Showdown. Revisa la conexion. & goto fin)
git checkout -q FETCH_HEAD || (popd & echo [ERROR] No pude cambiar de version de Showdown. & goto fin)
call npm install --omit=optional || (popd & echo [ERROR] Fallo npm install. & goto fin)
node build || (popd & echo [ERROR] Fallo la compilacion de Showdown. & goto fin)
popd
echo.
echo [2/4] Formatos de Champions que conoce el Showdown actualizado:
python -m vgclab.regulacion --listar-formatos
echo.
echo   Si la nueva regulacion aun no aparece, Showdown todavia no la tiene: vuelve en unos dias.
echo   (Pulsa Intro sin escribir nada para salir sin cambiar de regulacion.)
echo.
set "REGID="
set "REGNOMBRE="
set "REGFMT="
set /p REGID=  Id corto de la nueva regulacion (p. ej. md): 
if "%REGID%"=="" goto fin
set /p REGNOMBRE=  Nombre para mostrar (p. ej. Reg M-D): 
set /p REGFMT=  Formato Bo1 de la lista de arriba (el que no acaba en bo3): 
if "%REGFMT%"=="" goto fin
echo   Pestana de sus equipos en la hoja de VGCPastes. Intro = solo los que tienen resultados
echo   ("Champions M-D Featured Teams" para Reg M-D). Para todos, escribe p. ej.: Champions M-D
set "REGPEST="
set /p REGPEST=  Pestana (Intro = Featured Teams): 
if "%REGPEST%"=="" (
  python -m vgclab.regulacion --nueva %REGID% --nombre "%REGNOMBRE%" --formato %REGFMT%
) else (
  python -m vgclab.regulacion --nueva %REGID% --nombre "%REGNOMBRE%" --formato %REGFMT% --pestana "%REGPEST%"
)
if errorlevel 1 goto fin
echo.
echo [3/4] Equipos de la nueva regulacion...
if exist equipos_nuevos.txt move /y equipos_nuevos.txt equipos_nuevos_anterior.txt >nul
call :hacer_importar
echo.
echo [4/4] Listo. La app ya usa la nueva regulacion.
echo   Al principio de una regulacion casi no hay repeticiones de 1500+. Dentro de 2-3 semanas:
echo     opcion 1 (rutina mensual) para el juez y opcion 9 para crear su banco limpio.
echo   Despues copia el proyecto a la AYN Thor para que use lo mismo.
goto fin

:subir
echo.
echo Cambios del mantenimiento que se van a guardar y subir:
git status --short -- teams teams_apartados engine/juez.json engine/experiencia.json regulacion.json
set "HOY="
for /f "delims=" %%d in ('python -c "import datetime; print(datetime.date.today().isoformat())"') do set "HOY=%%d"
REM Solo lo que cambia el mantenimiento: equipos, juez, experiencia y regulacion (nada de lo que dejes suelto en la carpeta)
for %%p in (teams teams_apartados engine\juez.json engine\experiencia.json regulacion.json) do if exist "%%p" git add -A -- "%%p"
git commit -q -m "Mantenimiento %HOY% (%REGACTUAL%)" || echo   No habia cambios nuevos que guardar.
REM -u origin HEAD: sube la rama actual y la enlaza con GitHub aunque sea la primera vez
git push -u origin HEAD || (echo [ERROR] No pude subir a GitHub. Copia el mensaje de arriba para ver que paso. & goto fin)
echo.
echo Subido. En la Thor toca el acceso "RooBot - actualizar".
goto fin

:hacer_importar
REM Con hoja de equipos (regulacion.json): se importan de ahi los nuevos, y tambien lo que haya en equipos_nuevos.txt
python -m vgclab.regulacion --tiene-hoja >nul 2>nul
if errorlevel 1 goto sin_hoja
echo Equipos nuevos de la hoja de la regulacion...
if exist equipos_nuevos.txt (python -m vgclab.importar --hoja equipos_nuevos.txt) else (python -m vgclab.importar --hoja)
goto sprites_equipos
:sin_hoja
if exist equipos_nuevos.txt goto hay_archivo
echo # Pega aqui tus equipos y guarda el archivo. Las lineas que empiezan por # se ignoran.> equipos_nuevos.txt
echo # Vale: enlaces de pokepast.es, uno por linea, o equipos exportados de Showdown.>> equipos_nuevos.txt
echo # Para varios equipos de Showdown, pon antes de cada uno una linea:  === Nombre ===>> equipos_nuevos.txt
echo He creado equipos_nuevos.txt y lo abro. Pega tus equipos, guardalo y cierralo para seguir.
start "" /wait notepad equipos_nuevos.txt
:hay_archivo
python -m vgclab.importar equipos_nuevos.txt
:sprites_equipos
echo.
echo Sprites de los equipos (solo baja los que faltan)...
python -m vgclab.sprites
exit /b 0

:fin
echo.
pause
goto menu
