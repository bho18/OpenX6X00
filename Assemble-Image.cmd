@echo off
setlocal
cd /d "%~dp0"
if exist "x6200-persistent-voice-test.img.xz" (echo Image already exists. Verify it against SHA256SUMS. & pause & exit /b 1)
if not exist "x6200-persistent-voice-test.img.xz.part01" (echo Missing x6200-persistent-voice-test.img.xz.part01. Download and extract the complete GitHub ZIP. & pause & exit /b 1)
if not exist "x6200-persistent-voice-test.img.xz.part02" (echo Missing x6200-persistent-voice-test.img.xz.part02. Download and extract the complete GitHub ZIP. & pause & exit /b 1)
if not exist "x6200-persistent-voice-test.img.xz.part03" (echo Missing x6200-persistent-voice-test.img.xz.part03. Download and extract the complete GitHub ZIP. & pause & exit /b 1)
if not exist "x6200-persistent-voice-test.img.xz.part04" (echo Missing x6200-persistent-voice-test.img.xz.part04. Download and extract the complete GitHub ZIP. & pause & exit /b 1)
if not exist "x6200-persistent-voice-test.img.xz.part05" (echo Missing x6200-persistent-voice-test.img.xz.part05. Download and extract the complete GitHub ZIP. & pause & exit /b 1)
copy /b "x6200-persistent-voice-test.img.xz.part01"+"x6200-persistent-voice-test.img.xz.part02"+"x6200-persistent-voice-test.img.xz.part03"+"x6200-persistent-voice-test.img.xz.part04"+"x6200-persistent-voice-test.img.xz.part05" "x6200-persistent-voice-test.img.xz" >nul
if errorlevel 1 (echo Assembly failed. & pause & exit /b 1)
powershell -NoProfile -Command "$h=(Get-FileHash -LiteralPath 'x6200-persistent-voice-test.img.xz' -Algorithm SHA256).Hash; if($h -ne '9f1cf21ddc48e62aa94dd439fcc8d5f84db8d277dcadc19a0ce6b7ed25287aa9'){Write-Error 'Checksum mismatch. Do not use this image.';exit 1}; Write-Host 'Verified SD image is ready for your spare card.'"
pause
