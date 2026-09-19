"""
Marketing DiMango — agente de marketing dentro de Maximus.

Módulo ADITIVO: no toca el cerebro operativo ni sus herramientas. Vive aparte,
se apoya en lo que ya existe (memoria, Telegram, datos de venta, visión) y
manda todo a aprobación humana antes de publicar.

Piezas:
  · insights.py       — métricas reales de Instagram y Facebook (solo lectura)
  · banco_fotos.py    — las fotos que manda Ricardo, guardadas y etiquetadas
  · propuestas.py     — qué conviene publicar, cruzando venta + fotos + fecha
"""
