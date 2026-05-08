@echo off
chcp 65001 >nul
echo ============================================
echo   MT5 Copy Trader — Сборка EXE
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
%PYTHON% -m PyInstaller --onefile --windowed --name MT5CopyTrader --collect-all MetaTrader5 --collect-all numpy --hidden-import copier --hidden-import psutil --hidden-import tkinter --hidden-import tkinter.ttk --hidden-import tkinter.filedialog --hidden-import tkinter.messagebox gui.py

if %errorlevel% neq 0 (
    echo ОШИБКА: сборка не удалась
    pause
    exit /b 1
)

echo.
echo [3/3] Готово!
echo ============================================
echo   Файл: dist\MT5CopyTrader.exe
echo   Скопируйте его куда угодно и запускайте
echo ============================================
echo.
pause