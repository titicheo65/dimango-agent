@echo off
REM Wrapper para el Programador de tareas de Windows (ServidorPlaya).
REM Corre el script de subida de venta y guarda la salida en logs\task_runner.log
cd /d "%~dp0"
python subir_venta_mall.py >> "logs\task_runner.log" 2>&1
