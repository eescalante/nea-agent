"""Fork Quasar — responder por nota de voz cuando el lead mandó una nota de voz."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.llm import LlmReply
from app.profile import BusinessProfile, profile_from_payload
from app.prompt import AVISO_RESPUESTA_EN_AUDIO, partes_del_prompt
from app.state import Conversation, InboundMessage
from app.turn import debe_responder_con_audio, es_turno_de_audio, texto_para_audio
from tests.conftest import CRM_URL, mock_crm_basics, wa_body

AUDIO = InboundMessage(wa_message_id="w1", identity="1", type="audio", media_voice=True)
TEXTO = InboundMessage(wa_message_id="w2", identity="1", type="text", text="hola")
ON = BusinessProfile(voice=True, voice_reply_to_audio=True)


def _rt(voice_sent=None, booking=None):
    return SimpleNamespace(voice_sent=voice_sent, booking_confirmation=booking)


def test_caso_verdadero():
    assert debe_responder_con_audio(ON, [TEXTO, AUDIO], _rt(), "Claro, te cuento rápido.") is True


@pytest.mark.parametrize(
    "perfil",
    [
        BusinessProfile(voice=True, voice_reply_to_audio=False),
        BusinessProfile(voice=False, voice_reply_to_audio=True),
        BusinessProfile(),
    ],
)
def test_ajuste_apagado_o_sin_voz(perfil):
    assert debe_responder_con_audio(perfil, [AUDIO], _rt(), "hola") is False


def test_rafaga_sin_audio():
    assert debe_responder_con_audio(ON, [TEXTO], _rt(), "hola") is False
    assert es_turno_de_audio(ON, [TEXTO]) is False


@pytest.mark.parametrize("texto", ["Mira https://x.com/a", "http://x.com", "entra a www.x.com hoy", "", "   ", None])
def test_con_url_o_vacio(texto):
    assert debe_responder_con_audio(ON, [AUDIO], _rt(), texto) is False


def test_largo_y_limite():
    assert debe_responder_con_audio(ON, [AUDIO], _rt(), "a" * 801) is False
    assert debe_responder_con_audio(ON, [AUDIO], _rt(), "a" * 800) is True


def test_el_modelo_ya_mando_audio_o_hay_confirmacion_de_cita():
    assert debe_responder_con_audio(ON, [AUDIO], _rt(voice_sent="listo"), "hola") is False
    assert debe_responder_con_audio(ON, [AUDIO], _rt(booking={"x": 1}), "hola") is False


def test_limpieza_de_markdown():
    t = "**Hola** _Ana_:\n- uno\n- dos\n# Título\nMira [esto](https://x.com) `ya`"
    s = texto_para_audio(t)
    assert not any(c in s for c in "*_#`") and "http" not in s
    assert s == "Hola Ana: uno dos Título Mira esto ya"


def test_parseo_del_ajuste():
    p = lambda v: profile_from_payload({"profile": {"voiceReplyToAudio": v}}, "Nea").voice_reply_to_audio
    assert p(True) is True
    assert p("true") is False
    assert p(1) is False
    assert profile_from_payload({"profile": {}}, "Nea").voice_reply_to_audio is False
    assert profile_from_payload({}, "Nea").voice_reply_to_audio is False


def test_la_linea_va_solo_en_el_bloque_vivo():
    def partes(**kw):
        return partes_del_prompt(
            profile=BusinessProfile(agent_name="Angie"),
            context=None,
            conv=Conversation(id=1, wa_identity="x", greeted=True),
            **kw,
        )

    fijo, vivo = partes(respuesta_en_audio=True)
    assert AVISO_RESPUESTA_EN_AUDIO in vivo and "tu respuesta saldrá como nota de voz" not in fijo
    fijo0, vivo0 = partes()
    assert "nota de voz" not in vivo0.lower().replace("nota de voz del lead", "")
    assert fijo0 == fijo


def _con_perfil(ctx, perfil):
    async def get():
        return perfil

    ctx.profile = SimpleNamespace(get=get)


async def test_turno_de_audio_responde_con_voz_y_no_manda_texto(ctx, client, respx_mock):
    routes = mock_crm_basics(respx_mock)
    voz = respx_mock.post(f"{CRM_URL}/api/bot/voz").mock(return_value=httpx.Response(200, json={"messageId": "m"}))
    _con_perfil(ctx, ON)
    ctx.llm.replies = [LlmReply(content="**Claro**, te cuento rápido.")]
    await client.post("/webhook", content=wa_body(msg_type="audio", wamid="wamid.v1"))
    await asyncio.sleep(0.4)
    assert voz.call_count == 1
    assert b"Claro, te cuento" in voz.calls[0].request.content and b"*" not in voz.calls[0].request.content
    assert routes["messages"].call_count == 0
    guardados = [m.content for m in ctx.store.messages if m.role == "assistant"]
    assert len(guardados) == 1 and guardados[0].startswith("[Nota de voz enviada] Claro, te cuento")


async def test_si_falla_el_audio_cae_a_texto(ctx, client, respx_mock):
    routes = mock_crm_basics(respx_mock)
    respx_mock.post(f"{CRM_URL}/api/bot/voz").mock(return_value=httpx.Response(409, json={"error": "limite_de_audios"}))
    _con_perfil(ctx, ON)
    ctx.llm.replies = [LlmReply(content="Claro, te cuento rápido.")]
    await client.post("/webhook", content=wa_body(msg_type="audio", wamid="wamid.v2"))
    await asyncio.sleep(0.4)
    assert routes["messages"].call_count == 1


async def test_apagado_todo_sigue_por_texto(ctx, client, respx_mock):
    routes = mock_crm_basics(respx_mock)
    voz = respx_mock.post(f"{CRM_URL}/api/bot/voz").mock(return_value=httpx.Response(200, json={}))
    _con_perfil(ctx, BusinessProfile(voice=True))
    await client.post("/webhook", content=wa_body(msg_type="audio", wamid="wamid.v3"))
    await asyncio.sleep(0.4)
    assert voz.call_count == 0 and routes["messages"].call_count == 1


def test_el_audio_del_despacho_cuenta_aunque_no_traiga_el_flag_de_voz():
    from app.dispatch import mensajes_del_despacho

    msgs = mensajes_del_despacho({
        "contact": {"identity": "521111111111"},
        "messages": [{"id": "m1", "type": "audio", "mediaId": "med1", "mimeType": "audio/ogg"}],
    })
    assert msgs[0].media_voice is False and msgs[0].type == "audio"
    assert es_turno_de_audio(ON, msgs) is True
    assert es_turno_de_audio(BusinessProfile(voice=True), msgs) is False
    assert debe_responder_con_audio(ON, msgs, _rt(), "hola") is True


async def test_por_el_camino_de_despacho_un_audio_responde_con_voz(respx_mock):
    import hashlib
    import hmac
    import json

    from app.dispatch_worker import drain_one
    from app.main import create_app
    from tests.conftest import make_ctx, make_settings

    ctx = make_ctx(make_settings(vocero_mode="cloud", crm_organization="a", crm_brain_secret="test-brain-secret"))
    routes = mock_crm_basics(respx_mock)
    voz = respx_mock.post(f"{CRM_URL}/api/bot/voz").mock(return_value=httpx.Response(200, json={"messageId": "m"}))
    _con_perfil(ctx, ON)
    ctx.llm.replies = [LlmReply(content="Claro, te cuento rápido.")]
    data = {
        "dispatchId": "dsp_voz", "organization": {"id": "org_a", "slug": "a"},
        "conversation": {"id": "cv_a"}, "contact": {"identity": "521111111111"},
        "messages": [{"id": "msg_a", "type": "audio", "mediaId": "med1", "mimeType": "audio/ogg"}],
    }
    body = json.dumps(data).encode()
    firma = hmac.new(b"test-brain-secret", body, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(ctx)), base_url="http://test") as c:
        r = await c.post("/vocero/dispatch", content=body, headers={"x-vocero-signature": "sha256=" + firma})
        assert r.status_code == 200
        assert await drain_one(ctx)
    assert voz.call_count == 1
    assert routes["messages"].call_count == 0
    await ctx.crm.aclose()


def test_si_la_herramienta_de_voz_falla_o_se_uso_no_se_espeja():
    assert debe_responder_con_audio(ON, [AUDIO], SimpleNamespace(voice_sent=None, voice_failed=True), "no pude") is False
    assert debe_responder_con_audio(ON, [AUDIO], SimpleNamespace(voice_sent=None, voice_attempted=True), "listo") is False


async def _despacho_con_herramienta(respx_mock, status_voz):
    import hashlib
    import hmac
    import json

    from app.dispatch_worker import drain_one
    from app.llm import ToolCall
    from app.main import create_app
    from tests.conftest import make_ctx, make_settings

    ctx = make_ctx(make_settings(vocero_mode="cloud", crm_organization="a", crm_brain_secret="test-brain-secret"))
    routes = mock_crm_basics(respx_mock)
    voz = respx_mock.post(f"{CRM_URL}/api/bot/voz").mock(
        return_value=httpx.Response(status_voz, json={"messageId": "m"} if status_voz == 200 else {"error": "meta_unavailable"})
    )
    _con_perfil(ctx, ON)
    ctx.llm.replies = [
        LlmReply(content=None, tool_calls=[ToolCall(id="t1", name="send_voice_note", arguments={"text": "Hola, aquí va el audio."})]),
        LlmReply(content="No pude mandarte el audio, te respondo por texto."),
    ]
    data = {
        "dispatchId": "dsp_t", "organization": {"id": "org_a", "slug": "a"},
        "conversation": {"id": "cv_a"}, "contact": {"identity": "521111111111"},
        "messages": [{"id": "msg_a", "type": "audio", "mediaId": "med1", "mimeType": "audio/ogg"}],
    }
    body = json.dumps(data).encode()
    firma = hmac.new(b"test-brain-secret", body, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(ctx)), base_url="http://test") as c:
        assert (await c.post("/vocero/dispatch", content=body, headers={"x-vocero-signature": "sha256=" + firma})).status_code == 200
        assert await drain_one(ctx)
    await ctx.crm.aclose()
    return voz, routes


async def test_herramienta_de_voz_fallida_la_disculpa_va_por_texto(respx_mock):
    voz, routes = await _despacho_con_herramienta(respx_mock, 502)
    assert voz.call_count == 1  # solo el intento de la herramienta, sin segundo audio
    assert routes["messages"].call_count == 1


async def test_herramienta_de_voz_ok_no_manda_texto_ni_segundo_audio(respx_mock):
    voz, routes = await _despacho_con_herramienta(respx_mock, 200)
    assert voz.call_count == 1
    assert routes["messages"].call_count == 0
