"""Reglas de negocio de MS5 (Actividades y Notificaciones). Sin BD ni HTTP."""
import datetime
from decimal import Decimal

from .comun import DatoInvalido, ReglaNoCumplida, leer_iso

TIPOS = ('llamada', 'reunion', 'tarea', 'email', 'nota')
ESTADOS = ('pendiente', 'completada', 'cancelada', 'vencida')
# Tiempo máximo para el primer contacto según la temperatura del lead. None = sin plazo.
SLA_HORAS = {'caliente': 24, 'tibio': 72, 'frio': None}
DURACION_MAXIMA_REUNION = datetime.timedelta(hours=8)
UMBRAL_AVISO_GERENTE = Decimal('20000')

TITULOS_HISTORIAL = {
    'usuario.creado': 'Usuario dado de alta',
    'lead.asignado': 'Lead asignado a un vendedor',
    'lead.convertido': 'Lead convertido en cliente',
    'oportunidad.etapa_cambiada': 'Oportunidad cambió de etapa',
    'oportunidad.ganada': 'Oportunidad ganada',
    'actividad.vencida': 'Actividad vencida sin atender',
}


def validar_actividad(datos):
    tipo = datos.get('tipo')
    if tipo not in TIPOS:
        raise DatoInvalido('tipo_invalido', f'Tipos: {", ".join(TIPOS)}')
    titulo = (datos.get('titulo') or '').strip()
    if not titulo:
        raise DatoInvalido('campo_obligatorio', 'titulo es obligatorio')
    actividad = {'tipo': tipo, 'titulo': titulo[:150], 'ref': datos.get('ref'), 'notas': datos.get('notas')}
    if tipo == 'reunion':
        if not datos.get('inicio') or not datos.get('fin'):
            raise DatoInvalido('reunion_sin_horario', 'Una reunión necesita inicio y fin')
        inicio, fin = leer_iso(datos['inicio'], 'inicio'), leer_iso(datos['fin'], 'fin')
        if fin <= inicio:
            raise DatoInvalido('horario_invalido', 'fin debe ser posterior a inicio')
        if fin - inicio > DURACION_MAXIMA_REUNION:
            raise DatoInvalido('reunion_muy_larga', 'Una reunión dura como máximo 8 horas')
        actividad.update(inicio=inicio, fin=fin, vence_en=fin)
    elif datos.get('vence_en'):
        actividad['vence_en'] = leer_iso(datos['vence_en'], 'vence_en')
    return actividad


def se_cruzan(inicio_a, fin_a, inicio_b, fin_b):
    """Dos reuniones chocan si una empieza antes de que termine la otra (los bordes no chocan)."""
    return inicio_a < fin_b and inicio_b < fin_a


def validar_sin_choques(nueva_inicio, nueva_fin, reuniones_del_vendedor):
    for r in reuniones_del_vendedor:
        if r.get('estado') in ('pendiente', 'vencida') and se_cruzan(
                nueva_inicio, nueva_fin, leer_iso(r['inicio']), leer_iso(r['fin'])):
            raise ReglaNoCumplida('agenda_cruzada', {'choca_con': r['actividad_id'], 'titulo': r['titulo']})


def vence_primer_contacto(temperatura, asignado_en):
    horas = SLA_HORAS.get(temperatura)
    return None if horas is None else asignado_en + datetime.timedelta(hours=horas)


def validar_cambio_estado(actual, nuevo):
    if nuevo not in ('completada', 'cancelada'):
        raise DatoInvalido('estado_invalido', 'Solo se puede marcar como completada o cancelada')
    if actual in ('completada', 'cancelada'):
        raise ReglaNoCumplida('actividad_cerrada', f'La actividad ya está {actual}')


def esta_vencida(estado, vence_en, ahora):
    return estado == 'pendiente' and vence_en is not None and vence_en < ahora


def requiere_aviso_gerente(monto):
    return Decimal(str(monto or 0)) > UMBRAL_AVISO_GERENTE


def referencias_de_evento(datos):
    """A qué fichas del historial pertenece un evento (lead, cuenta y/o usuario)."""
    refs = []
    if datos.get('lead_id'):
        refs.append(f'lead-{datos["lead_id"]}')
    if datos.get('cuenta_id'):
        refs.append(f'cuenta-{datos["cuenta_id"]}')
    if not refs and datos.get('email'):
        refs.append(f'usuario-{datos["email"]}')
    return refs


def titulo_historial(evento, datos):
    titulo = TITULOS_HISTORIAL.get(evento, evento)
    if evento == 'oportunidad.etapa_cambiada':
        titulo = f'{titulo}: {datos.get("desde")} -> {datos.get("hacia")}'
    return titulo
