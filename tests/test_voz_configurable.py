"""Fork Quasar — las reglas de voz del agente se cambian sin recompilar."""
from __future__ import annotations

import os
import time

from app.profile import BusinessProfile
from app.prompt import VOZ_POR_DEFECTO, _business_block, _chassis, reglas_de_voz

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


# --- Una sola fuente de verdad por tema ---------------------------------------------------

def test_con_tono_del_negocio_el_chasis_no_dice_tu_ni_emojis_para_no_competir(monkeypatch):
    _limpio(monkeypatch)
    perfil = BusinessProfile(agent_name="Quasar", tone="Profesional, con voseo rioplatense. Sin emojis.")
    chasis = _chassis(perfil)
    assert "Sin tuteo ni voseo" not in chasis  # el trato del chasis ya no pelea con el voseo del perfil
    assert "Emojis: pocos" not in chasis
    assert "los define el tono del negocio" in chasis
    # y el tono del negocio es el único texto sobre esos temas (va en el bloque del perfil).
    assert "Tono definido por el negocio: Profesional, con voseo rioplatense." in _business_block(perfil)


def test_sin_tono_del_negocio_entra_el_estilo_por_defecto(monkeypatch):
    _limpio(monkeypatch)
    chasis = _chassis(BusinessProfile(agent_name="Quasar"))
    assert "Sin tuteo ni voseo" in chasis and "Emojis: pocos" in chasis
    assert "los define el tono del negocio" not in chasis


def test_un_tono_solo_con_espacios_cuenta_como_sin_tono(monkeypatch):
    _limpio(monkeypatch)
    assert "Sin tuteo ni voseo" in _chassis(BusinessProfile(agent_name="Quasar", tone="   "))


def test_las_universales_valen_con_y_sin_tono(monkeypatch):
    _limpio(monkeypatch)
    for perfil in (BusinessProfile(agent_name="Q"), BusinessProfile(agent_name="Q", tone="Formal.")):
        chasis = _chassis(perfil)
        for clave in ("Eres un agente de IA", "UNA pregunta por mensaje", "NATURAL:", "bien recibido", "Sin euforia", "No te describas con género"):
            assert clave in chasis, clave
        assert "NUNCA:" in chasis


def test_el_archivo_o_la_variable_siguen_reemplazando_todo_el_bloque_aunque_haya_tono(monkeypatch):
    _limpio(monkeypatch)
    monkeypatch.setenv("AGENT_VOICE_RULES", "- Solo esto.")
    chasis = _chassis(BusinessProfile(agent_name="Q", tone="Formal."))
    assert "- Solo esto." in chasis and "NATURAL:" not in chasis


def test_el_trato_por_defecto_no_es_tu_ni_voseo_y_el_ejemplo_de_confirmar_cita_tampoco_tutea(monkeypatch):
    _limpio(monkeypatch)
    chasis = _chassis(BusinessProfile(agent_name="Quasar"))
    assert "Sin tuteo ni voseo" in chasis and "usted" in chasis and 'de "tú"' not in chasis
    assert "te aparto" not in chasis  # un ejemplo con tuteo empujaría al modelo a tutear
    # Con tono del negocio, el trato lo decide el tono (no se menciona).
    assert "Sin tuteo ni voseo" not in _chassis(BusinessProfile(agent_name="Q", tone="Voseo cordial."))
