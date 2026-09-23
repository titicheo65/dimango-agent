@echo off
REM Marketing DiMango — la corrida de cada dia (D-018).
REM
REM Propone publicaciones por Telegram y NO publica nada: todo pasa por
REM aprobacion de Ricardo. Esa es la regla del proyecto.
REM
REM El directorio de trabajo tiene que ser la raiz del agente: load_dotenv()
REM busca el .env ahi, y sin .env no hay ni claves ni Telegram. Es el mismo
REM detalle que hizo fallar a ejecutar_advisor.bat la primera vez.
cd /d C:\dimango-agent
if not exist logs mkdir logs
python scripts\marketing_diario.py >> logs\marketing_diario.log 2>&1
