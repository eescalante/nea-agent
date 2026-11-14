"""El prompt de sistema en dos bloques sin marca (la caché la decide el CRM)."""
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


def test_sin_bloques_un_solo_texto_como_siempre():
    fijo, vivo = _partes()
    m = mensaje_de_sistema(fijo, vivo, False)
    assert m == {"role": "system", "content": fijo + vivo}


def test_en_bloques_dos_textos_sin_marca():
    fijo, vivo = _partes()
    m = mensaje_de_sistema(fijo, vivo, True)
    assert m["role"] == "system"
    b = m["content"]
    assert b == [{"type": "text", "text": fijo}, {"type": "text", "text": vivo}]
    assert all("cache_control" not in x for x in b)
    # El CRM, cuando no marca, une los bloques con "" (sin separador).
    assert "".join(x["text"] for x in b) == fijo + vivo


def test_en_bloques_y_sin_contexto_vivo_no_manda_un_bloque_vacio():
    m = mensaje_de_sistema("fijo", "", True)
    assert m["content"] == [{"type": "text", "text": "fijo"}]


def test_solo_va_en_bloques_con_el_proxy_del_crm():
    assert OpenAiLlm("k", "m", base_url="https://crm/api/bot/llm").mensaje_en_bloques
    assert OpenAiLlm("k", "m", base_url="https://crm/api/bot/llm/").mensaje_en_bloques
    # OpenRouter directo u otro compatible: nadie marcaría los bloques, un solo texto.
    assert not OpenAiLlm("k", "m", base_url="https://openrouter.ai/api/v1").mensaje_en_bloques
    # Contra OpenAI directo, un solo texto como siempre.
    assert not OpenAiLlm("k", "m", base_url=None).mensaje_en_bloques


def test_la_variable_vieja_ya_no_existe(monkeypatch):
    base = dict(vocero_mode="dispatch", crm_base_url="https://crm.example.com/", crm_bot_api_key="b")
    monkeypatch.setenv("LLM_CACHE_CONTROL", "true")
    assert not hasattr(Settings(**base), "llm_cache_control")


def test_el_pedido_lleva_los_bloques_sin_marca():
    # `complete()` manda los mensajes tal cual los arma el turno.
    class _C:
        def __init__(self):
            self.llamadas = []

        async def create(self, **kw):
            self.llamadas.append(kw)
            msg = SimpleNamespace(content="hola", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)

    import asyncio

    llm = OpenAiLlm("k", "m", base_url="https://crm/api/bot/llm")
    comp = _C()
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    fijo, vivo = _partes()
    msgs = [mensaje_de_sistema(fijo, vivo, llm.mensaje_en_bloques), {"role": "user", "content": "hola"}]
    asyncio.run(llm.complete(msgs))
    enviado = comp.llamadas[0]["messages"][0]["content"]
    assert isinstance(enviado, list) and "cache_control" not in enviado[0]
