import datetime

import pytest

from src import reglas
from src.comun import DatoInvalido, ReglaNoCumplida

UTC = datetime.timezone.utc


def t(hora, minuto=0):
    return datetime.datetime(2026, 11, 10, hora, minuto, tzinfo=UTC)


def test_reunion_necesita_horario_valido():
    with pytest.raises(DatoInvalido):
        reglas.validar_actividad({'tipo': 'reunion', 'titulo': 'Demo'})
    with pytest.raises(DatoInvalido):
        reglas.validar_actividad({'tipo': 'reunion', 'titulo': 'Demo',
                                  'inicio': '2026-11-10T10:00:00Z', 'fin': '2026-11-10T09:00:00Z'})
    actividad = reglas.validar_actividad({'tipo': 'reunion', 'titulo': 'Demo',
                                          'inicio': '2026-11-10T10:00:00Z', 'fin': '2026-11-10T11:00:00Z'})
    assert actividad['vence_en'] == t(11)


def test_reuniones_que_se_cruzan():
    assert reglas.se_cruzan(t(10), t(11), t(10, 30), t(12))
    assert not reglas.se_cruzan(t(10), t(11), t(11), t(12))  # una termina cuando empieza la otra


def test_choque_de_agenda_ignora_canceladas():
    existentes = [
        {'actividad_id': 'A1', 'titulo': 'Visita', 'estado': 'cancelada', 'inicio': '2026-11-10T10:00:00Z', 'fin': '2026-11-10T11:00:00Z'},
        {'actividad_id': 'A2', 'titulo': 'Demo', 'estado': 'pendiente', 'inicio': '2026-11-10T14:00:00Z', 'fin': '2026-11-10T15:00:00Z'},
    ]
    reglas.validar_sin_choques(t(10), t(11), existentes)
    with pytest.raises(ReglaNoCumplida):
        reglas.validar_sin_choques(t(14, 30), t(16), existentes)


def test_plazo_de_primer_contacto_por_temperatura():
    assert reglas.vence_primer_contacto('caliente', t(9)) == t(9) + datetime.timedelta(hours=24)
    assert reglas.vence_primer_contacto('tibio', t(9)) == t(9) + datetime.timedelta(hours=72)
    assert reglas.vence_primer_contacto('frio', t(9)) is None


def test_actividad_cerrada_no_cambia():
    with pytest.raises(ReglaNoCumplida):
        reglas.validar_cambio_estado('completada', 'cancelada')
    reglas.validar_cambio_estado('vencida', 'completada')


def test_actividad_completada_no_se_elimina():
    with pytest.raises(ReglaNoCumplida):
        reglas.validar_eliminacion('completada')
    for estado in ('pendiente', 'vencida', 'cancelada'):
        reglas.validar_eliminacion(estado)


def test_aviso_al_gerente_por_encima_de_20000():
    assert reglas.requiere_aviso_gerente(20000.01)
    assert not reglas.requiere_aviso_gerente(20000)


def test_referencias_del_historial():
    assert reglas.referencias_de_evento({'lead_id': 'L1', 'cuenta_id': 7}) == ['lead-L1', 'cuenta-7']
    assert reglas.referencias_de_evento({'email': 'ana@x.pe'}) == ['usuario-ana@x.pe']
