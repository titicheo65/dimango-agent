@echo off
REM Laboratorio Creativo — una vez por semana, sabado (D-018).
REM
REM Propone UNA prueba concreta con su costo al frente. No contrata ni gasta
REM nada: Ricardo aprueba antes de cualquier servicio externo.
REM
REM Mismo cuidado que el diario: el directorio de trabajo debe ser la raiz del
REM agente para que load_dotenv() encuentre el .env.
cd /d C:\dimango-agent
if not exist logs mkdir logs
python scripts\laboratorio_creativo.py >> logs\laboratorio_creativo.log 2>&1
