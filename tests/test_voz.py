"""Fork Quasar — Responder con una nota de voz (herramienta send_voice_note)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.crm import CrmError, CrmUnreachable
from app.profile import BusinessProfile, profile_from_payload
from app.tools import ToolRuntime, tool_schemas


def _nombres(schemas):
    return [t["function"]["name"] for t in schemas]


class _Crm:
    def __init__(self, falla: bool = False, sin_respuesta: bool = False):
        self._sin_respuesta = sin_respuesta
        self.llamadas: list[tuple[str, str]] = []
        self._falla = falla

    async def send_voice(self, conversation_id: str, text: str):
        if self._sin_respuesta:
            raise CrmUnreachable("timeout")
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
    res = asyncio.run(rt.execute("send_voice_note", {"text": "x" * 801}))
    assert res["ok"] is False and "800" in res["detalle"]
    assert crm.llamadas == [] and rt.voice_sent is None
    # Puede reintentar con uno corto en el mismo turno.
    assert asyncio.run(rt.execute("send_voice_note", {"text": "corto"}))["ok"] is True


def test_si_el_crm_no_contesta_a_tiempo_no_se_le_dice_al_cliente_que_no_se_puede():
    # En producción el CRM tardaba más que el tope y el audio llegaba igual: Nea decía
    # «no puedo mandar audios» justo antes de que llegara.
    rt = _runtime(_Crm(sin_respuesta=True))
    res = asyncio.run(rt.execute("send_voice_note", {"text": "hola"}))
    assert res["ok"] is True
    assert "NO afirmes" in res["nota"]
    assert rt.voice_sent == "hola"


def test_el_pedido_de_voz_espera_mas_que_los_demas():
    from app.crm import VOICE_TIMEOUT_SECONDS

    assert VOICE_TIMEOUT_SECONDS > 15.0


def test_despues_del_audio_el_turno_termina_sin_otra_ronda_ni_texto():
    from app.turn import _tool_loop

    class _Llm:
        def __init__(self):
            self.rondas = 0

        async def complete(self, messages, tools=None):
            self.rondas += 1
            return SimpleNamespace(
                content=None,
                tool_calls=[SimpleNamespace(id="t1", name="send_voice_note", arguments={"text": "Hola, te cuento."})],
            )

    crm = _Crm()
    llm = _Llm()
    ctx = SimpleNamespace(crm=crm, llm=llm, agenda_enabled=False)
    rt = ToolRuntime(ctx, None, "cv_1", profile=BusinessProfile(voice=True))
    texto = asyncio.run(_tool_loop(ctx, [{"role": "user", "content": "mandame un audio"}], rt))
    assert texto is None  # nada que mandar por texto
    assert llm.rondas == 1  # no hay una tercera llamada al modelo
    assert crm.llamadas == [("cv_1", "Hola, te cuento.")]


def test_si_el_audio_falla_el_turno_sigue_y_contesta_por_texto():
    from app.turn import _tool_loop

    class _Llm:
        def __init__(self):
            self.rondas = 0

        async def complete(self, messages, tools=None):
            self.rondas += 1
            if self.rondas == 1:
                return SimpleNamespace(
                    content=None,
                    tool_calls=[SimpleNamespace(id="t1", name="send_voice_note", arguments={"text": "hola"})],
                )
            return SimpleNamespace(content="Te lo cuento por texto.", tool_calls=[])

    ctx = SimpleNamespace(crm=_Crm(falla=True), llm=_Llm(), agenda_enabled=False)
    rt = ToolRuntime(ctx, None, "cv_1", profile=BusinessProfile(voice=True))
    texto = asyncio.run(_tool_loop(ctx, [{"role": "user", "content": "mandame un audio"}], rt))
    assert texto == "Te lo cuento por texto."
    assert ctx.llm.rondas == 2


def test_el_cronometro_se_le_manda_al_crm_en_una_llamada_aparte():
    # Fork Quasar: el CRM anota lo que tardó cada paso de Nea junto con lo que él mide.
    import httpx

    from app.crm import CrmClient
    from app.turn import _Cron

    visto: dict = {}

    def responder(request: httpx.Request) -> httpx.Response:
        import json

        visto["ruta"] = request.url.path
        visto["cuerpo"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    cliente = CrmClient("http://crm.test", "k", client=httpx.AsyncClient(
        base_url="http://crm.test", transport=httpx.MockTransport(responder)))
    cron = _Cron()
    cron.marca("conv")
    cron.marca("ctx")
    asyncio.run(cliente.post_cronometro("cv_1", cron.pasos()))
    assert visto["ruta"] == "/api/bot/cronometro"
    assert visto["cuerpo"]["conversationId"] == "cv_1"
    assert set(visto["cuerpo"]["pasos"]) == {"conv", "ctx"}
