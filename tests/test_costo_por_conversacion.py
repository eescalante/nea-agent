"""Costo por conversación: el id de la conversación del CRM viaja al proxy del CRM."""
import asyncio
from types import SimpleNamespace

from app.llm import ENCABEZADO_CONVERSACION, OpenAiLlm, conversacion_actual


class _Completions:
    def __init__(self):
        self.llamadas: list[dict] = []

    async def create(self, **kwargs):
        self.llamadas.append(kwargs)
        mensaje = SimpleNamespace(content="hola", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=mensaje)], usage=None)


def _llm(base_url):
    llm = OpenAiLlm("k", "m/uno", base_url=base_url)
    comp = _Completions()
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    return llm, comp


def _pedir(llm, conversacion):
    async def _():
        conversacion_actual.set(conversacion)
        return await llm.complete([{"role": "user", "content": "hola"}])

    # Un contexto propio por corrida, como un turno real.
    return asyncio.run(_())


def test_hacia_el_proxy_del_crm_lleva_la_conversacion():
    llm, comp = _llm("https://crm.example.com/api/bot/llm")
    _pedir(llm, "cv_abc123")
    assert comp.llamadas[0]["extra_headers"] == {ENCABEZADO_CONVERSACION: "cv_abc123"}


def test_sin_conversacion_no_manda_encabezado():
    llm, comp = _llm("https://crm.example.com/api/bot/llm")
    _pedir(llm, None)
    assert "extra_headers" not in comp.llamadas[0]


def test_a_un_proveedor_directo_no_se_le_manda_nada():
    for base in (None, "https://openrouter.ai/api/v1", "https://api.openai.com/v1"):
        llm, comp = _llm(base)
        _pedir(llm, "cv_abc123")
        assert "extra_headers" not in comp.llamadas[0], base


def test_la_conversacion_de_un_turno_no_se_queda_para_el_siguiente():
    llm, comp = _llm("https://crm.example.com/api/bot/llm/")
    _pedir(llm, "cv_uno")
    _pedir(llm, None)
    assert "extra_headers" in comp.llamadas[0]
    assert "extra_headers" not in comp.llamadas[1]
