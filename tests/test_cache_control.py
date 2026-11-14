"""Marca de caché de Anthropic en el prompt de sistema (LLM_CACHE_CONTROL)."""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.config import Settings
from app.llm import OpenAiLlm
from app.profile import BusinessProfile
from app.prompt import build_system_prompt, mensaje_de_sistema, partes_del_prompt
from app.state import Conversation

AHORA = datetime(2026, 9, 10, 13, 6, tzinfo=timezone.utc)


def _partes(**kw):
    return partes_del_prompt(
        profile=BusinessProfile(agent_name="Angie", kb_text="Horario: 9 a 18."),
        context={"contact": {"name": "Ana"}},
        conv=Conversation(id=1, wa_identity="x", greeted=True),
        now=AHORA,
        **kw,
    )


def test_el_texto_es_exactamente_el_de_siempre():
    fijo, vivo = _partes()
    entero = build_system_prompt(
        profile=BusinessProfile(agent_name="Angie", kb_text="Horario: 9 a 18."),
        context={"contact": {"name": "Ana"}},
        conv=Conversation(id=1, wa_identity="x", greeted=True),
        now=AHORA,
    )
    assert fijo + vivo == entero


def test_lo_fijo_no_trae_nada_que_cambie_por_turno():
    fijo, vivo = _partes()
    # (El chasis cita la frase «CONTEXTO ACTUAL» en una regla; lo que cambia es el bloque.)
    assert "\nCONTEXTO ACTUAL:" in vivo and "Fecha y hora" in vivo and "Ana" in vivo
    assert "\nCONTEXTO ACTUAL:" not in fijo and "Fecha y hora" not in fijo and "Ana" not in fijo
    # Lo fijo se repite igual a otra hora y con otro lead: ese es el prefijo de la caché.
    otro, _ = partes_del_prompt(
        profile=BusinessProfile(agent_name="Angie", kb_text="Horario: 9 a 18."),
        context={"contact": {"name": "Beto"}},
        conv=Conversation(id=2, wa_identity="y", greeted=False),
        now=datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc),
    )
    assert otro == fijo


def test_sin_cache_un_solo_texto_como_siempre():
    fijo, vivo = _partes()
    m = mensaje_de_sistema(fijo, vivo, False)
    assert m == {"role": "system", "content": fijo + vivo}


def test_con_cache_dos_bloques_y_la_marca_solo_en_el_fijo():
    fijo, vivo = _partes()
    m = mensaje_de_sistema(fijo, vivo, True)
    assert m["role"] == "system"
    b = m["content"]
    assert [x["type"] for x in b] == ["text", "text"]
    assert b[0]["text"] == fijo and b[0]["cache_control"] == {"type": "ephemeral"}
    assert b[1]["text"] == vivo and "cache_control" not in b[1]
    assert "".join(x["text"] for x in b) == fijo + vivo


def test_con_cache_y_sin_contexto_vivo_no_manda_un_bloque_vacio():
    m = mensaje_de_sistema("fijo", "", True)
    assert len(m["content"]) == 1


def test_solo_se_activa_con_un_proveedor_compatible():
    assert OpenAiLlm("k", "m", base_url="https://crm/api/bot/llm", cache_control=True).cache_control_activo
    assert OpenAiLlm("k", "m", base_url="https://openrouter.ai/api/v1", cache_control=True).cache_control_activo
    # Contra OpenAI directo nunca: su API rechaza los campos que no conoce.
    assert not OpenAiLlm("k", "m", base_url=None, cache_control=True).cache_control_activo
    # Apagado por defecto.
    assert not OpenAiLlm("k", "m", base_url="https://crm/api/bot/llm").cache_control_activo


def test_la_variable_de_entorno(monkeypatch):
    base = dict(vocero_mode="dispatch", crm_base_url="https://crm.example.com/", crm_bot_api_key="b")
    assert Settings(**base).llm_cache_control is False
    monkeypatch.setenv("LLM_CACHE_CONTROL", "true")
    assert Settings(**base).llm_cache_control is True


def test_el_pedido_lleva_los_bloques_solo_si_esta_activo():
    # `complete()` manda los mensajes tal cual los arma el turno.
    class _C:
        def __init__(self):
            self.llamadas = []

        async def create(self, **kw):
            self.llamadas.append(kw)
            msg = SimpleNamespace(content="hola", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)

    import asyncio

    llm = OpenAiLlm("k", "m", base_url="https://crm/api/bot/llm", cache_control=True)
    comp = _C()
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    fijo, vivo = _partes()
    msgs = [mensaje_de_sistema(fijo, vivo, llm.cache_control_activo), {"role": "user", "content": "hola"}]
    asyncio.run(llm.complete(msgs))
    enviado = comp.llamadas[0]["messages"][0]["content"]
    assert isinstance(enviado, list) and enviado[0]["cache_control"] == {"type": "ephemeral"}
