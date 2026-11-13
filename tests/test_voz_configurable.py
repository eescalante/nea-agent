"""Fork Quasar — las reglas de voz del agente se cambian sin recompilar."""
from __future__ import annotations

import os
import time

from app.profile import BusinessProfile
from app.prompt import VOZ_POR_DEFECTO, _chassis, reglas_de_voz

PERFIL = BusinessProfile(agent_name="Quasar")


def _limpio(monkeypatch):
    monkeypatch.delenv("AGENT_VOICE_RULES", raising=False)
    monkeypatch.delenv("AGENT_VOICE_RULES_FILE", raising=False)


def test_sin_configuracion_usa_las_de_siempre(monkeypatch):
    _limpio(monkeypatch)
    assert reglas_de_voz() == VOZ_POR_DEFECTO
    assert "NATURAL:" in _chassis(PERFIL) and "bien recibido" in _chassis(PERFIL)


def test_la_variable_de_entorno_reemplaza_el_estilo_y_acepta_saltos_con_barra_n(monkeypatch):
    _limpio(monkeypatch)
    monkeypatch.setenv("AGENT_VOICE_RULES", "- Voseo siempre.\\n- Nunca exclamaciones de entusiasmo.")
    chasis = _chassis(PERFIL)
    assert "- Voseo siempre.\n- Nunca exclamaciones de entusiasmo." in chasis
    assert "NATURAL:" not in chasis  # lo de siempre se fue


def test_el_archivo_gana_a_la_variable_y_se_relee_cuando_cambia(monkeypatch, tmp_path):
    _limpio(monkeypatch)
    f = tmp_path / "voz.md"
    f.write_text("- Primera versión.")
    monkeypatch.setenv("AGENT_VOICE_RULES_FILE", str(f))
    monkeypatch.setenv("AGENT_VOICE_RULES", "- de la variable")
    assert reglas_de_voz() == "- Primera versión."
    f.write_text("- Segunda versión, sin reiniciar nada.")
    os.utime(f, (time.time() + 5, time.time() + 5))  # mtime distinto
    assert reglas_de_voz() == "- Segunda versión, sin reiniciar nada."


def test_un_archivo_ilegible_o_vacio_cae_a_la_variable_o_a_las_de_siempre(monkeypatch, tmp_path):
    _limpio(monkeypatch)
    monkeypatch.setenv("AGENT_VOICE_RULES_FILE", str(tmp_path / "no-existe.md"))
    assert reglas_de_voz() == VOZ_POR_DEFECTO
    vacio = tmp_path / "vacio.md"
    vacio.write_text("   \n")
    monkeypatch.setenv("AGENT_VOICE_RULES_FILE", str(vacio))
    monkeypatch.setenv("AGENT_VOICE_RULES", "- de la variable")
    assert reglas_de_voz() == "- de la variable"


def test_cambiar_la_voz_no_toca_los_nunca_ni_el_resto_del_chasis(monkeypatch):
    _limpio(monkeypatch)
    monkeypatch.setenv("AGENT_VOICE_RULES", "- Cualquier cosa.")
    chasis = _chassis(PERFIL)
    assert "NUNCA:" in chasis and "CONVERSACIÓN:" in chasis and "IDENTIDAD Y VOZ:" in chasis
