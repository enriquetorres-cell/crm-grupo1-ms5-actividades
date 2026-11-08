"""MS5 · Actividades y Notificaciones: agenda, historial del cliente y avisos por email.

DynamoDB:
  actividades (tenant_id, actividad_id) con 2 índices dispersos (sparse):
    reuniones   gsi_vendedor = <tenant>#<vendedor>, inicio  -> detectar choques de agenda
    pendientes  gsi_estado = 'pendiente', vence_en           -> encontrar las vencidas
  historial (ref_key = <tenant>#<ref>, orden = <fecha>#<evento_id>)

Consume (cola crm-ms5): usuario.creado, lead.asignado, lead.convertido,
oportunidad.etapa_cambiada, oportunidad.ganada, actividad.vencida.
Publica: actividad.vencida (tarea programada cada hora).
"""
import os
import uuid

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from . import reglas
from .comun import (NoEncontrado, ReglaNoCumplida, ahora, consultar_pagina, enrutar, iso, leer_cuerpo, leer_iso,
                    log, notificar, parametro, procesar_cola, publicar, puede_ver, responder, ruta)

_dynamo = boto3.resource('dynamodb')
actividades = _dynamo.Table(os.environ['TABLA_ACTIVIDADES'])
historial = _dynamo.Table(os.environ['TABLA_HISTORIAL'])


def _guardar_actividad(tenant_id, vendedor, datos, creado_por):
    actividad = {
        'tenant_id': tenant_id, 'actividad_id': f'A{uuid.uuid4().hex[:12]}', 'vendedor': vendedor,
        'estado': 'pendiente', 'creado_por': creado_por, 'creado_en': iso(ahora()),
        **{k: v for k, v in datos.items() if v is not None},
    }
    for campo in ('inicio', 'fin', 'vence_en'):
        if campo in actividad:
            actividad[campo] = iso(actividad[campo])
    if 'vence_en' in actividad:
        actividad['gsi_estado'] = 'pendiente'
    if actividad['tipo'] == 'reunion':
        actividad['gsi_vendedor'] = f'{tenant_id}#{vendedor}'
    actividades.put_item(Item=actividad)
    return actividad


def _sin_campos_internos(actividad):
    return {k: v for k, v in actividad.items() if not k.startswith('gsi_')}


# ---------- Rutas ----------

def salud(event, ctx):
    return responder(200, {'status': 'ok', 'servicio': 'ms5'})


def crear_actividad(event, ctx):
    cuerpo = leer_cuerpo(event)
    datos = reglas.validar_actividad(cuerpo)
    # Un gerente puede agendar a otro vendedor; un vendedor solo a sí mismo.
    vendedor = cuerpo.get('vendedor') if ctx['rol'] in ('gerente', 'admin') and cuerpo.get('vendedor') else ctx['usuario_id']
    if datos['tipo'] == 'reunion':
        posibles = actividades.query(
            IndexName='reuniones',
            KeyConditionExpression=Key('gsi_vendedor').eq(f'{ctx["tenant_id"]}#{vendedor}') & Key('inicio').lt(iso(datos['fin'])),
            FilterExpression=Attr('fin').gt(iso(datos['inicio'])),
        )['Items']
        reglas.validar_sin_choques(datos['inicio'], datos['fin'], posibles)
    actividad = _guardar_actividad(ctx['tenant_id'], vendedor, datos, ctx['usuario_id'])
    return responder(201, _sin_campos_internos(actividad))


def listar_actividades(event, ctx):
    filtro = None
    if parametro(event, 'estado'):
        filtro = Attr('estado').eq(parametro(event, 'estado'))
    if ctx['rol'] == 'vendedor':
        propio = Attr('vendedor').eq(ctx['usuario_id'])
        filtro = propio if filtro is None else filtro & propio
    kwargs = {'KeyConditionExpression': Key('tenant_id').eq(ctx['tenant_id'])}
    if filtro is not None:
        kwargs['FilterExpression'] = filtro
    pagina = consultar_pagina(actividades, event, **kwargs)
    pagina['items'] = [_sin_campos_internos(a) for a in pagina['items']]
    return responder(200, pagina)


def cambiar_estado(event, ctx):
    nuevo = leer_cuerpo(event).get('estado')
    actividad = actividades.get_item(Key={'tenant_id': ctx['tenant_id'], 'actividad_id': ruta(event, 'id')}).get('Item')
    if not actividad or not puede_ver(ctx, actividad['vendedor']):
        raise NoEncontrado('actividad_no_encontrada', ruta(event, 'id'))
    reglas.validar_cambio_estado(actividad['estado'], nuevo)
    try:
        # Se quita del índice de pendientes: ya no puede vencer.
        actividad = actividades.update_item(
            Key={'tenant_id': ctx['tenant_id'], 'actividad_id': actividad['actividad_id']},
            UpdateExpression='SET estado = :nuevo, cerrado_en = :ahora REMOVE gsi_estado',
            ConditionExpression='estado = :actual',
            ExpressionAttributeValues={':nuevo': nuevo, ':actual': actividad['estado'], ':ahora': iso(ahora())},
            ReturnValues='ALL_NEW',
        )['Attributes']
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise ReglaNoCumplida('estado_cambiado', 'La actividad cambió; vuelve a cargarla')
        raise
    return responder(200, _sin_campos_internos(actividad))


def ver_historial(event, ctx):
    """Historial de una ficha: ref = lead-<id> | cuenta-<id> | usuario-<email>."""
    return responder(200, consultar_pagina(
        historial, event,
        KeyConditionExpression=Key('ref_key').eq(f'{ctx["tenant_id"]}#{ruta(event, "ref")}'),
        ScanIndexForward=False,
    ))


RUTAS = {
    'GET /ms5/health': salud,
    'GET /ms5/actividades': listar_actividades,
    'POST /ms5/actividades': crear_actividad,
    'PATCH /ms5/actividades/{id}/estado': cambiar_estado,
    'GET /ms5/historial/{ref}': ver_historial,
}


def main(event, _context):
    return enrutar(RUTAS, event)


# ---------- Consumidor de eventos ----------

def _registrar_en_historial(mensaje):
    """Devuelve False si el evento ya estaba (SQS lo entregó de nuevo): así nada se procesa dos veces."""
    datos = mensaje['datos']
    refs = reglas.referencias_de_evento(datos)
    nuevo = False
    for ref in refs:
        try:
            historial.put_item(
                Item={
                    'ref_key': f'{mensaje["tenant_id"]}#{ref}',
                    'orden': f'{mensaje["ocurrido_en"]}#{mensaje["id"]}',
                    'evento': mensaje['evento'], 'titulo': reglas.titulo_historial(mensaje['evento'], datos),
                    'ocurrido_en': mensaje['ocurrido_en'], 'datos': datos,
                },
                ConditionExpression='attribute_not_exists(orden)',
            )
            nuevo = True
        except ClientError as e:
            if e.response['Error']['Code'] != 'ConditionalCheckFailedException':
                raise
    return nuevo or not refs


def _manejar(mensaje):
    if not _registrar_en_historial(mensaje):
        return
    evento, datos, tenant_id = mensaje['evento'], mensaje['datos'], mensaje['tenant_id']

    if evento == 'lead.asignado' and datos.get('propietario'):
        vence = reglas.vence_primer_contacto(datos.get('temperatura'), leer_iso(datos['asignado_en']))
        if vence:
            _guardar_actividad(tenant_id, datos['propietario'], {
                'tipo': 'llamada', 'titulo': f'Primer contacto con {datos.get("empresa")}',
                'ref': f'lead-{datos["lead_id"]}', 'vence_en': vence,
            }, creado_por='sistema')

    elif evento == 'oportunidad.ganada' and reglas.requiere_aviso_gerente(datos.get('monto')):
        notificar(
            f'[{tenant_id}] Venta ganada: {datos.get("titulo")}',
            f'{datos.get("propietario")} ganó "{datos.get("titulo")}" por S/ {datos.get("monto")}.',
            tenant_id,
        )

    elif evento == 'actividad.vencida':
        notificar(
            f'[{tenant_id}] Actividad vencida',
            f'"{datos.get("titulo")}" de {datos.get("vendedor")} venció el {datos.get("vence_en")} sin atenderse.',
            tenant_id,
        )


def consumidor(event, _context):
    return procesar_cola(event, _manejar)


# ---------- Tarea programada: marca vencidas y publica actividad.vencida ----------

def revisar_vencidas(event, _context):
    momento = ahora()
    kwargs = {'IndexName': 'pendientes',
              'KeyConditionExpression': Key('gsi_estado').eq('pendiente') & Key('vence_en').lt(iso(momento))}
    vencidas = 0
    while True:
        respuesta = actividades.query(**kwargs)
        for item in respuesta['Items']:
            if not reglas.esta_vencida(item['estado'], leer_iso(item['vence_en']), momento):
                continue
            try:
                actividades.update_item(
                    Key={'tenant_id': item['tenant_id'], 'actividad_id': item['actividad_id']},
                    UpdateExpression='SET estado = :vencida REMOVE gsi_estado',
                    ConditionExpression='estado = :pendiente',
                    ExpressionAttributeValues={':vencida': 'vencida', ':pendiente': 'pendiente'},
                )
            except ClientError as e:
                if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                    continue  # la completaron justo ahora
                raise
            publicar('actividad.vencida', item['tenant_id'], {
                'actividad_id': item['actividad_id'], 'titulo': item['titulo'], 'vendedor': item['vendedor'],
                'vence_en': item['vence_en'], 'lead_id': (item.get('ref') or '').removeprefix('lead-') or None,
            })
            vencidas += 1
        if 'LastEvaluatedKey' not in respuesta:
            break
        kwargs['ExclusiveStartKey'] = respuesta['LastEvaluatedKey']
    log.info('Actividades marcadas como vencidas: %s', vencidas)
    return {'vencidas': vencidas}
