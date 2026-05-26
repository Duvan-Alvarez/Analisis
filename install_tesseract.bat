@echo off
echo Instalando Tesseract OCR para Windows...
echo.

echo Descargando Tesseract OCR...
powershell -Command "& {Invoke-WebRequest -Uri 'https://github.com/UB-Mannheim/tesseract/wiki/Downloading-Tesseract-OCR' -OutFile 'tesseract_download.html'}"
echo.
echo Por favor, descarga manualmente Tesseract desde:
echo https://github.com/UB-Mannheim/tesseract/wiki
echo.
echo Selecciona la version de 64-bit para Windows.
echo Instala en la ruta por defecto (C:\Program Files\Tesseract-OCR\)
echo.
echo Despues de instalar, agrega a PATH:
echo C:\Program Files\Tesseract-OCR\
echo.
echo Presiona cualquier tecla para verificar la instalacion...
pause >nul

tesseract --version
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Tesseract no se encuentra en PATH.
    echo Agrega 'C:\Program Files\Tesseract-OCR\' a las variables de entorno PATH.
    echo.
    pause
    exit /b 1
)

echo.
echo Tesseract instalado correctamente!
echo Ahora puedes usar OCR en la aplicacion CVision.
pause