"""Fork Quasar: el CRM le pide a Nea reiniciar una conversación o recargar el perfil.

Dos eventos nuevos por la misma ruta firmada de los despachos:

- `conversation.reset`: olvida la memoria de UNA conversación (sin borrarla en
  el CRM) y relee el perfil, igual que el comando /reset.
- `agent.refresh`: solo relee el perfil, sin tocar ninguna conversación.
"""
from __future__ import annotations

import json

from app.dispatch_worker import drain_one
from tests.test_olvido import cliente, cloud, con_memoria, enviar, firmar

CUERPO_REINICIO = json.dumps(
    {
        "type": "conversation.reset",
        "eventId": "rst_1",
        "organization": {"id": "org_gemela", "slug": "gemela"},
        "conversation": {"id": "cv_gemela1", "channel": "whatsapp"},
        "contact": {"id": "ct_gemelo1", "identity": "5215550001111"},
        "resetAt": "2026-11-13T12:00:00.000Z",
    },
    separators=(",", ":"),
).encode()

CUERPO_RECARGA = json.dumps(
    {
        "type": "agent.refresh",
        "eventId": "rfs_1",
        "organization": {"id": "org_gemela", "slug": "gemela"},
        "requestedAt": "2026-11-13T12:00:00.000Z",
    },
    separators=(",", ":"),
).encode()


class PerfilFalso:
    """Un proveedor de perfil que solo cuenta cuántas veces lo vencieron."""

    def __init__(self) -> None:
        self.refrescos = 0

    def refrescar(self) -> None:
        self.refrescos += 1


async def test_el_reinicio_borra_la_memoria_y_relee_el_perfil():
    ctx = cloud()
    ctx.profile = PerfilFalso()
    await con_memoria(ctx)
    async with cliente(ctx) as client:
        resp = await enviar(client, CUERPO_REINICIO)
        assert resp.status_code == 200
        # Acusar recibo no es haber olvidado: eso lo hace la cola.
        assert len(ctx.store.conversations) == 1
        assert ctx.profile.refrescos == 0
        assert await drain_one(ctx)
    assert ctx.store.conversations == {}
    assert ctx.store.messages == []
    assert ctx.profile.refrescos == 1
    await ctx.crm.aclose()


async def test_el_reinicio_con_firma_invalida_no_toca_nada():
    ctx = cloud()
    ctx.profile = PerfilFalso()
    await con_memoria(ctx)
    async with cliente(ctx) as client:
        resp = await enviar(client, CUERPO_REINICIO, firmar(CUERPO_REINICIO, "otro-secreto"))
        assert resp.status_code == 401
        assert not await drain_one(ctx)
    assert len(ctx.store.conversations) == 1
    assert ctx.profile.refrescos == 0
    await ctx.crm.aclose()


async def test_la_recarga_vence_el_perfil_sin_tocar_conversaciones():
    ctx = cloud()
    ctx.profile = PerfilFalso()
    await con_memoria(ctx)
    async with cliente(ctx) as client:
        resp = await enviar(client, CUERPO_RECARGA)
        assert resp.status_code == 200
        # No pasa por la cola: no hay nada que atender.
        assert not await drain_one(ctx)
    assert ctx.profile.refrescos == 1
    assert len(ctx.store.conversations) == 1
    await ctx.crm.aclose()


async def test_la_recarga_sin_firma_valida_es_401():
    ctx = cloud()
    ctx.profile = PerfilFalso()
    async with cliente(ctx) as client:
        resp = await enviar(client, CUERPO_RECARGA, firmar(CUERPO_RECARGA, "otro-secreto"))
        assert resp.status_code == 401
    assert ctx.profile.refrescos == 0
    await ctx.crm.aclose()


async def test_la_recarga_sin_event_id_es_422():
    ctx = cloud()
    ctx.profile = PerfilFalso()
    cuerpo = json.dumps({"type": "agent.refresh", "organization": {"id": "org_gemela"}}).encode()
    async with cliente(ctx) as client:
        resp = await enviar(client, cuerpo)
        assert resp.status_code == 422
    assert ctx.profile.refrescos == 0
    await ctx.crm.aclose()
