"""Fork Quasar: lo que no depende de lo demás no hace esperar al turno.

Al empezar un turno Nea le hace al CRM tres consultas independientes entre sí:
el aviso de «escribiendo…», la sonda de la agenda y el perfil. Antes iban una
detrás de otra. Ahora:

- el aviso va en segundo plano (el turno no usa su resultado, y con Zernio el
  CRM hace una llamada externa antes de contestar);
- la sonda y el perfil se piden a la vez (el prompt necesita las dos).

La llamada al modelo NO puede ir en paralelo con el perfil: el prompt se arma
con él.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import app.turn as turno
from app.state import InboundMessage
from app.turn import _Turno, run_turn
from tests.conftest import IDENTITY, crm_context, make_ctx


def _entrante() -> list[InboundMessage]:
    return [InboundMessage(wa_message_id="m1", identity=IDENTITY, type="text", text="hola")]


async def _ctx_listo(monkeypatch):
    ctx = make_ctx()
    conv = await ctx.store.get_or_create_conversation(IDENTITY)
    await ctx.store.update_conversation(conv.id, crm_conversation_id="cv_1")
    monkeypatch.setattr(
        "app.turn._fetch_context", AsyncMock(return_value=crm_context(conv_id="cv_1"))
    )
    ctx.crm.send_message = AsyncMock(return_value={"messageId": "msg_1"})
    return ctx


async def test_el_aviso_de_escribiendo_no_frena_el_turno(monkeypatch):
    ctx = await _ctx_listo(monkeypatch)
    arranco = asyncio.Event()
    soltar = asyncio.Event()

    async def typing_lento(_conversation_id):
        arranco.set()
        await soltar.wait()  # tarda "para siempre": el turno no puede esperarlo

    ctx.crm.post_typing = typing_lento
    try:
        await asyncio.wait_for(
            run_turn(ctx, IDENTITY, _entrante(), _Turno(crm_conversation_id="cv_1")),
            timeout=3,
        )
        assert len(ctx.llm.calls) == 1  # el turno terminó y contestó
        await asyncio.wait_for(arranco.wait(), timeout=1)  # y el aviso sí salió
    finally:
        soltar.set()
        await asyncio.gather(*list(turno._TAREAS_DE_FONDO), return_exceptions=True)
        await ctx.crm.aclose()


async def test_un_fallo_del_aviso_no_rompe_el_turno(monkeypatch):
    ctx = await _ctx_listo(monkeypatch)
    ctx.crm.post_typing = AsyncMock(side_effect=RuntimeError("el CRM no pudo"))
    await run_turn(ctx, IDENTITY, _entrante(), _Turno(crm_conversation_id="cv_1"))
    await asyncio.gather(*list(turno._TAREAS_DE_FONDO), return_exceptions=True)
    assert len(ctx.llm.calls) == 1
    ctx.crm.post_typing.assert_awaited()
    await ctx.crm.aclose()


async def test_la_sonda_de_agenda_y_el_perfil_se_piden_a_la_vez(monkeypatch):
    ctx = await _ctx_listo(monkeypatch)
    ctx.crm.post_typing = AsyncMock()
    agenda_original, perfil_original = turno.agenda_vigente, turno.resolve_profile
    DEMORA = 0.3

    async def agenda_lenta(c):
        await asyncio.sleep(DEMORA)
        return await agenda_original(c)

    async def perfil_lento(c):
        await asyncio.sleep(DEMORA)
        return await perfil_original(c)

    monkeypatch.setattr("app.turn.agenda_vigente", agenda_lenta)
    monkeypatch.setattr("app.turn.resolve_profile", perfil_lento)

    inicio = time.monotonic()
    await run_turn(ctx, IDENTITY, _entrante(), _Turno(crm_conversation_id="cv_1"))
    duracion = time.monotonic() - inicio

    assert len(ctx.llm.calls) == 1
    # En serie serían >= 0.6 s; a la vez, ~0.3 s (con holgura para una máquina lenta).
    assert duracion < 0.55, f"se pidieron una detrás de otra ({duracion:.2f}s)"
    await asyncio.gather(*list(turno._TAREAS_DE_FONDO), return_exceptions=True)
    await ctx.crm.aclose()
