"""Fork Quasar — Responder con una nota de voz (herramienta send_voice_note)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.crm import CrmError
from app.profile import BusinessProfile, profile_from_payload
from app.tools import ToolRuntime, tool_schemas


def _nombres(schemas):
    return [t["function"]["name"] for t in schemas]


class _Crm:
    def __init__(self, falla: bool = False):
        self.llamadas: list[tuple[str, str]] = []
        self._falla = falla

    async def send_voice(self, conversation_id: str, text: str):
        if self._falla:
            raise CrmError("voz devolvió 502: voz_fallo")
        self.llamadas.append((conversation_id, text))
        return {"messageId": "msg_1"}


def _runtime(crm, voice: bool = True) -> ToolRuntime:
    ctx = SimpleNamespace(crm=crm)
    return ToolRuntime(ctx, None, "cv_1", profile=BusinessProfile(voice=voice))


def test_la_herramienta_solo_se_muestra_si_el_crm_la_habilito():
    assert "send_voice_note" not in _nombres(tool_schemas())
    assert "send_voice_note" in _nombres(tool_schemas(voz=True))


def test_la_descripcion_la_acota_a_cuando_el_cliente_la_pide():
    schema = next(t for t in tool_schemas(voz=True) if t["function"]["name"] == "send_voice_note")
    d = schema["function"]["description"]
    assert "expresamente" in d and "Nunca la ofrezcas" in d


def test_el_perfil_la_habilita_por_las_herramientas_del_crm():
    con = profile_from_payload({"profile": {"tools": ["ficha", "voz", "escalar"]}}, "Nea")
    sin = profile_from_payload({"profile": {"tools": ["ficha", "escalar"]}}, "Nea")
    assert con.voice is True and sin.voice is False
    assert profile_from_payload({}, "Nea").voice is False


def test_manda_el_audio_por_el_crm_y_recuerda_lo_que_dijo():
    crm = _Crm()
    rt = _runtime(crm)
    res = asyncio.run(rt.execute("send_voice_note", {"text": "  Hola, te cuento rápido.  "}))
    assert res["ok"] is True
    assert crm.llamadas == [("cv_1", "Hola, te cuento rápido.")]
    assert rt.voice_sent == "Hola, te cuento rápido."


def test_sin_habilitar_no_llama_al_crm():
    crm = _Crm()
    res = asyncio.run(_runtime(crm, voice=False).execute("send_voice_note", {"text": "hola"}))
    assert res["ok"] is False and crm.llamadas == []


def test_un_solo_audio_por_turno_y_texto_obligatorio():
    crm = _Crm()
    rt = _runtime(crm)
    assert asyncio.run(rt.execute("send_voice_note", {"text": " "}))["ok"] is False
    assert asyncio.run(rt.execute("send_voice_note", {"text": "uno"}))["ok"] is True
    assert asyncio.run(rt.execute("send_voice_note", {"text": "dos"}))["ok"] is False
    assert len(crm.llamadas) == 1


def test_si_el_crm_falla_el_modelo_recibe_que_conteste_por_texto():
    rt = _runtime(_Crm(falla=True))
    res = asyncio.run(rt.execute("send_voice_note", {"text": "hola"}))
    assert res["ok"] is False
    assert "texto" in res["detalle"]
    assert rt.voice_sent is None


def test_un_texto_demasiado_largo_se_devuelve_para_que_lo_acorte_y_no_cuenta_como_enviado():
    crm = _Crm()
    rt = _runtime(crm)
    res = asyncio.run(rt.execute("send_voice_note", {"text": "x" * 601}))
    assert res["ok"] is False and "600" in res["detalle"]
    assert crm.llamadas == [] and rt.voice_sent is None
    # Puede reintentar con uno corto en el mismo turno.
    assert asyncio.run(rt.execute("send_voice_note", {"text": "corto"}))["ok"] is True
