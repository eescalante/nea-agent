"""CA-7 (spec 068 de Nagai): en modo despacho, la conversación es la del CRM.

Con un CRM multilínea, el MISMO contacto que le escribe a dos números de la
misma empresa tiene dos conversaciones allá, una por línea. Nea tiene que
tratarlas como dos: dos memorias, dos fases, dos seguimientos, dos candados
que no se esperan entre sí. Y la red de seguridad del borrado no puede leer
«otra conversación del CRM» como «la anterior se eliminó»: es la otra línea.

El webhook directo (Meta → Nea, sin CRM que despache) sigue con la identidad
como clave: ahí no hay id de conversación hasta leer el contexto.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

import httpx

from app.dispatch import _procesar, linea_del_despacho, olvidar
from app.followup import FollowupWorker
from app.llm import LlmReply
from app.state import (
    CLAVE_CONVERSACION,
    CLAVE_IDENTIDAD,
    clave_de_conversacion,
    utcnow,
)
from app.turn import conversation_lock
from tests.conftest import CRM_URL, FakeLLM, crm_context, make_ctx, make_settings

CONTACTO = "5215512345678"
LINEA_A = "cv_linea_a"
LINEA_B = "cv_linea_b"


def _ctx_despacho(llm: FakeLLM | None = None):
    return make_ctx(make_settings(vocero_mode="dispatch"), llm=llm)


def _despacho(conversacion: str, texto: str, n: int, *, linea: dict | None = None) -> dict:
    cuerpo: dict[str, Any] = {
        "dispatchId": f"dsp_{conversacion}_{n}",
        "conversation": {"id": conversacion},
        "contact": {"identity": CONTACTO, "displayName": "Ana"},
        "messages": [{"id": f"msg_{conversacion}_{n}", "type": "text", "text": texto}],
    }
    if linea is not None:
        cuerpo["line"] = linea
    return cuerpo


def _crm_por_conversacion(respx_mock) -> dict[str, Any]:
    """Un CRM que contesta el contexto de la conversación que se le pide."""
    pedidas: list[str | None] = []
    enviadas: list[tuple[str, str]] = []

    def contexto(request: httpx.Request) -> httpx.Response:
        cid = request.url.params.get("conversationId")
        pedidas.append(cid)
        if cid not in (LINEA_A, LINEA_B):
            return httpx.Response(404, json={})
        return httpx.Response(200, json=crm_context(conv_id=cid))

    def mensaje(request: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(request.content)
        enviadas.append((cuerpo["conversationId"], cuerpo["text"]))
        return httpx.Response(200, json={"messageId": f"m{len(enviadas)}"})

    respx_mock.get(f"{CRM_URL}/api/bot/context").mock(side_effect=contexto)
    respx_mock.post(f"{CRM_URL}/api/bot/messages").mock(side_effect=mensaje)
    respx_mock.post(f"{CRM_URL}/api/bot/typing").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    respx_mock.put(f"{CRM_URL}/api/bot/ficha").mock(
        return_value=httpx.Response(200, json={"ficha": {}, "stageMoved": False})
    )
    respx_mock.post(f"{CRM_URL}/api/bot/handoff").mock(
        return_value=httpx.Response(200, json={})
    )
    return {"pedidas": pedidas, "enviadas": enviadas}


def _filas(ctx) -> dict[str, Any]:
    return {
        c.crm_conversation_id: c
        for c in ctx.store.conversations.values()
        if c.wa_identity == CONTACTO
    }


def _historial(ctx, conv_id: int) -> list[tuple[str, str]]:
    return [(m.role, m.content) for m in ctx.store.messages if m.conversation_id == conv_id]


# ------------------------------------------------------ memorias y fases ---


async def test_mismo_contacto_en_dos_lineas_son_dos_memorias(respx_mock):
    ctx = _ctx_despacho()
    ctx.llm.replies = [
        LlmReply(content="Hola, soy de la línea de ventas"),
        LlmReply(content="Hola, soy de la línea de soporte"),
        LlmReply(content="Seguimos con ventas"),
    ]
    crm = _crm_por_conversacion(respx_mock)

    await _procesar(ctx, _despacho(LINEA_A, "quiero comprar", 1))
    await _procesar(ctx, _despacho(LINEA_B, "tengo una falla", 1))
    await _procesar(ctx, _despacho(LINEA_A, "¿y el precio?", 2))

    filas = _filas(ctx)
    assert set(filas) == {LINEA_A, LINEA_B}
    a, b = filas[LINEA_A], filas[LINEA_B]
    assert a.id != b.id
    assert a.clave_por == b.clave_por == CLAVE_CONVERSACION

    # Cada memoria tiene solo lo suyo: la otra línea no entra en el prompt, y
    # el segundo mensaje de A no borró nada (la red del borrado no confundió
    # la línea B con «la conversación anterior se eliminó»).
    assert _historial(ctx, a.id) == [
        ("user", "quiero comprar"),
        ("assistant", "Hola, soy de la línea de ventas"),
        ("user", "¿y el precio?"),
        ("assistant", "Seguimos con ventas"),
    ]
    assert _historial(ctx, b.id) == [
        ("user", "tengo una falla"),
        ("assistant", "Hola, soy de la línea de soporte"),
    ]
    prompt_b = json.dumps(ctx.llm.calls[1]["messages"], ensure_ascii=False)
    assert "quiero comprar" not in prompt_b

    # El contexto se pide por conversación (por identidad el CRM no sabría
    # cuál de las dos devolver) y cada respuesta sale por su conversación.
    assert crm["pedidas"] == [LINEA_A, LINEA_B, LINEA_A]
    assert [c for c, _ in crm["enviadas"]] == [LINEA_A, LINEA_B, LINEA_A]

    # Las fases son independientes.
    await ctx.store.update_conversation(a.id, phase="agendando")
    assert ctx.store.conversations[a.id].phase == "agendando"
    assert ctx.store.conversations[b.id].phase == "descubrimiento"
    await ctx.crm.aclose()


async def test_horarios_ofrecidos_y_envios_diferidos_por_conversacion():
    ctx = _ctx_despacho()
    a = await ctx.store.get_or_create_conversation(CONTACTO, "", "", LINEA_A)
    b = await ctx.store.get_or_create_conversation(CONTACTO, "", "", LINEA_B)
    assert a.id != b.id
    otra_vez = await ctx.store.get_or_create_conversation(CONTACTO, "", "", LINEA_A)
    assert otra_vez.id == a.id

    from app.state import OfferedSlot

    await ctx.store.replace_offered_slots(
        a.id, [OfferedSlot(conversation_id=a.id, start_utc=utcnow(), end_utc=None, label="lun 10")]
    )
    assert await ctx.store.get_offered_slots(b.id) == []
    await ctx.store.enqueue_pending_send(a.id, LINEA_A, "respuesta de A")

    # Olvidar la línea A no toca la B.
    assert await ctx.store.forget_conversation("", CONTACTO, LINEA_A) is True
    assert set(_filas(ctx)) == {LINEA_B}
    assert ctx.store.pending_sends == {}
    await ctx.crm.aclose()


async def test_olvido_de_una_linea_deja_la_otra(respx_mock):
    ctx = _ctx_despacho()
    _crm_por_conversacion(respx_mock)
    await _procesar(ctx, _despacho(LINEA_A, "hola A", 1))
    await _procesar(ctx, _despacho(LINEA_B, "hola B", 1))

    await olvidar(ctx, {
        "type": "conversation.deleted", "eventId": "ev1", "dispatchId": "olvido:ev1",
        "conversation": {"id": LINEA_A}, "contact": {"identity": CONTACTO},
    })

    filas = _filas(ctx)
    assert set(filas) == {LINEA_B}
    assert _historial(ctx, filas[LINEA_B].id)[0] == ("user", "hola B")
    await ctx.crm.aclose()


async def test_seguimiento_de_cada_linea_por_su_conversacion(respx_mock):
    ctx = _ctx_despacho()
    ctx.llm.replies = [LlmReply(content="¿Seguimos?"), LlmReply(content="¿Seguimos?")]
    crm = _crm_por_conversacion(respx_mock)
    for cid in (LINEA_A, LINEA_B):
        conv = await ctx.store.get_or_create_conversation(CONTACTO, "", "", cid)
        await ctx.store.add_message(conv.id, "user", f"me interesa ({cid})")
        await ctx.store.update_conversation(
            conv.id, greeted=True, followup_due_at=utcnow() - timedelta(minutes=1)
        )

    await FollowupWorker(ctx).tick()

    assert sorted(crm["pedidas"]) == [LINEA_A, LINEA_B]
    assert sorted(c for c, _ in crm["enviadas"]) == [LINEA_A, LINEA_B]
    assert all(c.followup_sent for c in _filas(ctx).values())
    await ctx.crm.aclose()


# ------------------------------------------------------------- candados ---


async def test_candados_separados_por_conversacion():
    ctx = _ctx_despacho()
    async with conversation_lock(ctx, CONTACTO, LINEA_A):
        # La otra línea del mismo contacto entra sin esperar…
        async def otra_linea() -> None:
            async with conversation_lock(ctx, CONTACTO, LINEA_B):
                pass

        await asyncio.wait_for(otra_linea(), timeout=0.5)

        # …y la misma conversación sí espera.
        async def misma() -> None:
            async with conversation_lock(ctx, CONTACTO, LINEA_A):
                pass

        tarea = asyncio.create_task(misma())
        await asyncio.sleep(0.05)
        assert not tarea.done()
    await asyncio.wait_for(tarea, timeout=0.5)
    assert ctx.turn_locks == {}  # se reciclan al soltarse
    await ctx.crm.aclose()


class _LlmQueEspera(FakeLLM):
    """La primera llamada se queda pensando hasta que la suelten."""

    def __init__(self) -> None:
        super().__init__()
        self.soltar = asyncio.Event()

    async def complete(self, messages, tools=None):
        primera = not self.calls
        reply = await super().complete(messages, tools)
        if primera:
            await self.soltar.wait()
        return reply


async def test_dos_turnos_del_mismo_contacto_en_dos_lineas_corren_a_la_vez(respx_mock):
    llm = _LlmQueEspera()
    llm.replies = [LlmReply(content="respuesta A"), LlmReply(content="respuesta B")]
    ctx = _ctx_despacho(llm)
    crm = _crm_por_conversacion(respx_mock)

    turno_a = asyncio.create_task(_procesar(ctx, _despacho(LINEA_A, "hola A", 1)))
    for _ in range(100):
        if llm.calls:
            break
        await asyncio.sleep(0.01)
    assert llm.calls, "el turno A no llegó a pensar"

    # Con A todavía pensando (y su candado tomado), B termina entero.
    await asyncio.wait_for(_procesar(ctx, _despacho(LINEA_B, "hola B", 1)), timeout=2)
    assert crm["enviadas"] == [(LINEA_B, "respuesta B")]
    assert not turno_a.done()

    llm.soltar.set()
    await asyncio.wait_for(turno_a, timeout=2)
    assert crm["enviadas"] == [(LINEA_B, "respuesta B"), (LINEA_A, "respuesta A")]
    await ctx.crm.aclose()


# ----------------------------------------------------------- la línea ---


async def test_line_del_despacho_se_lee_si_viene_y_se_ignora_si_no(respx_mock, caplog):
    linea = {"id": "ln_1", "name": "Ventas", "channel": "whatsapp", "mode": "coexistencia"}
    assert linea_del_despacho({"line": linea}) == linea
    assert linea_del_despacho({}) is None
    assert linea_del_despacho({"line": "rota"}) is None
    assert linea_del_despacho({"line": {"name": "sin id"}}) is None

    ctx = _ctx_despacho()
    crm = _crm_por_conversacion(respx_mock)
    caplog.set_level("INFO", logger="nea.dispatch")
    await _procesar(ctx, _despacho(LINEA_A, "hola", 1, linea=linea))
    await _procesar(ctx, _despacho(LINEA_B, "hola", 1))
    assert [c for c, _ in crm["enviadas"]] == [LINEA_A, LINEA_B]
    assert any("línea ln_1" in r.getMessage() for r in caplog.records)
    await ctx.crm.aclose()


# --------------------------------------------- webhook directo intacto ---


async def test_webhook_directo_conserva_la_identidad_como_clave():
    ctx = make_ctx()  # sin VOCERO_MODE: Meta → Nea
    assert not ctx.settings.recibe_despachos
    assert clave_de_conversacion(ctx.settings, "", CONTACTO, LINEA_A) == (
        "", CLAVE_IDENTIDAD, CONTACTO
    )

    una = await ctx.store.get_or_create_conversation(CONTACTO)
    otra = await ctx.store.get_or_create_conversation(CONTACTO)
    assert una.id == otra.id and una.clave_por == CLAVE_IDENTIDAD

    # Aunque el turno ya sepa el id del CRM, el candado sigue siendo el de la
    # identidad: el id aparece a medio turno y cambiar de clave lo partiría.
    async with conversation_lock(ctx, CONTACTO, LINEA_A):
        tarea = asyncio.create_task(_tomar(ctx, CONTACTO, None))
        await asyncio.sleep(0.05)
        assert not tarea.done()
    await asyncio.wait_for(tarea, timeout=0.5)
    await ctx.crm.aclose()


async def _tomar(ctx, identidad: str, crm_id: str | None) -> None:
    async with conversation_lock(ctx, identidad, crm_id):
        pass
