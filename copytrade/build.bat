@echo off
chcp 65001 >nul
echo ============================================
echo   FTH Trade Copier — Сборка EXE
echo ============================================
echo.

set PYTHON=C:\Users\bu4ukeec\AppData\Local\Programs\Python\Python314\python.exe

echo [1/3] Установка зависимостей...
%PYTHON% -m pip install pyinstaller MetaTrader5 psutil
if %errorlevel% neq 0 (
    echo ОШИБКА: не удалось установить зависимости
    pause
    exit /b 1
)

echo.
echo [2/3] Сборка EXE (может занять 1-2 минуты)...
%PYTHON% -m PyInstaller --onefile --windowed --name FTHTradeCopier --icon=img/convertico-fth.ico --add-data "img;img" --collect-all MetaTrader5 --collect-all numpy --hidden-import copier --hidden-import psutil --hidden-import tkinter --hidden-import tkinter.ttk --hidden-import tkinter.filedialog --hidden-import tkinter.messagebox --hidden-import pystray --hidden-import pystray._win32 --hidden-import six gui.py

if %errorlevel% neq 0 (
    echo ОШИБКА: сборка не удалась
    pause
    exit /b 1
)

echo.
echo [3/3] Готово!
echo ============================================
echo   Файл: dist\FTHTradeCopier.exe
echo   Скопируйте его куда угодно и запускайте
echo ============================================
echo.
pause