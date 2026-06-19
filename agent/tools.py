# agent/tools.py — Herramientas del agente
# Generado por AgentKit

"""
Herramientas específicas del negocio Dimango.
Estas funciones extienden las capacidades del agente más allá de responder texto.
Generadas según los casos de uso elegidos: FAQ, reservaciones, leads/ventas,
pedidos, soporte post-venta y atención a proveedores (cotizaciones).
"""

import os
import yaml
import logging
from datetime import datetime

logger = logging.getLogger("agentkit")


def cargar_info_negocio() -> dict:
    """Carga la información del negocio desde business.yaml."""
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def obtener_horario() -> dict:
    """Retorna el horario de atención del negocio."""
    info = cargar_info_negocio()
    return {
        "horario": info.get("negocio", {}).get("horario", "No disponible"),
        "esta_abierto": True,  # TODO: calcular según hora actual y horario
    }


def buscar_en_knowledge(consulta: str) -> str:
    """
    Busca información relevante en los archivos de /knowledge.
    Retorna el contenido más relevante encontrado.
    """
    resultados = []
    knowledge_dir = "knowledge"

    if not os.path.exists(knowledge_dir):
        return "No hay archivos de conocimiento disponibles."

    for archivo in os.listdir(knowledge_dir):
        ruta = os.path.join(knowledge_dir, archivo)
        if archivo.startswith(".") or not os.path.isfile(ruta):
            continue
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                contenido = f.read()
                # Búsqueda simple por coincidencia de texto
                if consulta.lower() in contenido.lower():
                    resultados.append(f"[{archivo}]: {contenido[:500]}")
        except (UnicodeDecodeError, IOError):
            continue

    if resultados:
        return "\n---\n".join(resultados)
    return "No encontré información específica sobre eso en mis archivos."


# ════════════════════════════════════════════════════════════
# RESERVACIONES
# ════════════════════════════════════════════════════════════

def registrar_reserva(telefono: str, nombre: str, fecha: str, hora: str, personas: int) -> dict:
    """
    Registra una solicitud de reserva de mesa.
    El equipo de Dimango la confirmará por WhatsApp.
    """
    reserva = {
        "telefono": telefono,
        "nombre": nombre,
        "fecha": fecha,
        "hora": hora,
        "personas": personas,
        "estado": "pendiente",
        "creada": datetime.utcnow().isoformat(),
    }
    logger.info(f"Nueva reserva: {reserva}")
    return reserva


# ════════════════════════════════════════════════════════════
# PEDIDOS
# ════════════════════════════════════════════════════════════

def crear_pedido(telefono: str, items: list[dict]) -> dict:
    """
    Crea un pedido a partir de una lista de items.
    Cada item: {"producto": str, "cantidad": int, "precio": int}
    Retorna el pedido con el total calculado.
    """
    total = sum(item.get("precio", 0) * item.get("cantidad", 1) for item in items)
    pedido = {
        "telefono": telefono,
        "items": items,
        "total": total,
        "estado": "pendiente",
        "creado": datetime.utcnow().isoformat(),
    }
    logger.info(f"Nuevo pedido de {telefono}: total ${total}")
    return pedido


# ════════════════════════════════════════════════════════════
# LEADS / VENTAS
# ════════════════════════════════════════════════════════════

def registrar_lead(telefono: str, nombre: str, interes: str) -> dict:
    """Registra un cliente interesado (lead) para seguimiento de ventas."""
    lead = {
        "telefono": telefono,
        "nombre": nombre,
        "interes": interes,
        "estado": "nuevo",
        "creado": datetime.utcnow().isoformat(),
    }
    logger.info(f"Nuevo lead: {lead}")
    return lead


# ════════════════════════════════════════════════════════════
# SOPORTE POST-VENTA
# ════════════════════════════════════════════════════════════

def crear_ticket_soporte(telefono: str, asunto: str, detalle: str) -> dict:
    """Crea un ticket de soporte post-venta (dudas, reclamos, sugerencias)."""
    ticket = {
        "telefono": telefono,
        "asunto": asunto,
        "detalle": detalle,
        "estado": "abierto",
        "creado": datetime.utcnow().isoformat(),
    }
    logger.info(f"Nuevo ticket de soporte: {ticket}")
    return ticket


# ════════════════════════════════════════════════════════════
# PROVEEDORES / COTIZACIONES
# ════════════════════════════════════════════════════════════

def registrar_cotizacion_proveedor(telefono: str, empresa: str, producto: str,
                                    precio: str, condiciones: str) -> dict:
    """
    Registra una propuesta de un proveedor (cotización/presupuesto)
    para derivar al área de compras de Dimango.
    """
    cotizacion = {
        "telefono": telefono,
        "empresa": empresa,
        "producto": producto,
        "precio": precio,
        "condiciones": condiciones,
        "estado": "por revisar",
        "creada": datetime.utcnow().isoformat(),
    }
    logger.info(f"Nueva cotización de proveedor: {cotizacion}")
    return cotizacion
