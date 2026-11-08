# MS5 · Actividades y Notificaciones — CRM SaaS (CS2032 · Grupo 1)

Agenda (llamadas, reuniones, tareas) sin choques de horario, historial de cada ficha, plazos de primer contacto y avisos por email (**SNS**).
Base de datos: **DynamoDB**. Arquitectura: [crm-grupo1-infra](https://github.com/enriquetorres-cell/crm-grupo1-infra).

## Endpoints (`/ms5`, todos con token salvo health)

| Método | Ruta | Qué hace |
| --- | --- | --- |
| GET | `/ms5/health` | público |
| GET | `/ms5/actividades?limit=&siguiente=&estado=` | listado con cursor |
| POST | `/ms5/actividades` | crea; una reunión que choca con otra devuelve 409 |
| PATCH | `/ms5/actividades/{id}/estado` | `completada` o `cancelada` |
| GET | `/ms5/historial/{ref}` | `ref` = `lead-<id>`, `cuenta-<id>` o `usuario-<email>` |

## Eventos

- **Publica:** `actividad.vencida`, desde una tarea programada cada hora.
- **Consume (cola `crm-ms5`):** `usuario.creado`, `lead.asignado` (crea la tarea de primer contacto: caliente 24 h, tibio 72 h), `lead.convertido`, `oportunidad.etapa_cambiada`, `oportunidad.ganada` (email al gerente si supera S/ 20 000), `actividad.vencida` (email).

## Estructura DynamoDB

Tabla `crm-ms5-<stage>-actividades` (`tenant_id` + `actividad_id`), con 2 índices dispersos:
- `reuniones`: `gsi_vendedor` + `inicio`, para detectar choques.
- `pendientes`: `gsi_estado` + `vence_en`, para encontrar las vencidas.

```json
{"tenant_id":"tenant_andina","actividad_id":"A9f8e7d6c5b4a","tipo":"reunion","titulo":"Demo con Constructora Lima",
 "vendedor":"vendedor@andina.pe","estado":"pendiente","inicio":"2026-11-10T15:00:00Z","fin":"2026-11-10T16:00:00Z",
 "vence_en":"2026-11-10T16:00:00Z","ref":"lead-L1a2b3c4d5e6","creado_por":"vendedor@andina.pe",
 "gsi_vendedor":"tenant_andina#vendedor@andina.pe","gsi_estado":"pendiente"}
```

Tabla `crm-ms5-<stage>-historial` (`ref_key` + `orden`):

```json
{"ref_key":"tenant_andina#lead-L1a2b3c4d5e6","orden":"2026-11-08T15:00:00Z#<evento_id>","evento":"lead.convertido",
 "titulo":"Lead convertido en cliente","ocurrido_en":"2026-11-08T15:00:00Z","datos":{"…":"…"}}
```

## Desplegar

Requiere desplegados `crm-compartido` y `crm-ms1-usuarios`.

```bash
npm install
npx serverless deploy --stage dev
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

`src/comun.py` es una copia de `comun/comun.py` del repo infra: edítalo allá y corre `sincronizar-comun`.
