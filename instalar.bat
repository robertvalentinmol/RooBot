@echo off
REM ==========================================================================
REM  RooBot - instalacion en Windows
REM  Requisitos: Node.js 20+ (https://nodejs.org), Git (https://git-scm.com),
REM              Python 3.10+ (https://python.org, marca "Add to PATH")
REM ==========================================================================
setlocal
cd /d %~dp0
set COMMIT=d601443
if not "%VGCLAB_SHOWDOWN_COMMIT%"=="" set COMMIT=%VGCLAB_SHOWDOWN_COMMIT%

where node >nul 2>nul || (echo [ERROR] Falta Node.js 20+  -  https://nodejs.org & exit /b 1)
where git  >nul 2>nul || (echo [ERROR] Falta Git  -  https://git-scm.com & exit /b 1)
where python >nul 2>nul || (echo [ERROR] Falta Python 3.10+  -  https://python.org & exit /b 1)

echo.
echo [1/4] Descargando Pokemon Showdown (simulador oficial de Smogon)...
if not exist server\pokemon-showdown (
  git clone --filter=blob:none https://github.com/smogon/pokemon-showdown.git server\pokemon-showdown || exit /b 1
)
pushd server\pokemon-showdown
git checkout -q %COMMIT% 2>nul || echo     Aviso: no pude fijar la version probada %COMMIT%; uso la ultima.

echo [2/4] Compilando el simulador de Showdown (lo usa el motor de la app)...
call npm install --omit=optional || exit /b 1
node build || exit /b 1
popd

echo [3/4] Instalando dependencias de Python...
python -m pip install -r requirements.txt || exit /b 1
echo [4/4] Comprobando el motor...
echo {"id":1,"cmd":"ping"} | node engine\engine.js || exit /b 1
echo.
echo Instalacion completa. Siguiente paso:  app.bat  (y, si quieres, mantenimiento.bat opcion 8 para tener un icono en el escritorio)
endlocal
