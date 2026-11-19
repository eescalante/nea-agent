-- 009_conversacion_del_crm.sql — en modo despacho, la conversación es la del CRM.
--
-- ## El fallo que esta migración cierra
--
-- Desde 004 la fila de `bot_conversation` era única por (organización,
-- identidad). Con un CRM multilínea (Nagai, spec 068) eso deja de servir: el
-- MISMO contacto que le escribe a dos números de la misma empresa tiene DOS
-- conversaciones en el CRM, una por línea, y con la clave de 004 las dos
-- compartían memoria, fase, seguimiento, horarios ofrecidos y candado de turno.
-- Peor: la red de seguridad del borrado (`_olvidar_si_es_otra`) veía un id de
-- conversación distinto en cada mensaje y borraba la memoria de la otra línea.
--
-- ## La regla nueva
--
-- Cada fila dice por qué se identifica (`clave_por`):
--
-- - 'conversacion': modo despacho (`VOCERO_MODE=dispatch` o `cloud`). El CRM
--   manda en cada evento `conversation.id`, y esa es la clave: única por
--   (organización, crm_conversation_id). El mismo contacto en dos líneas son
--   dos filas.
-- - 'identidad': el webhook directo de Meta (sin CRM que despache). Ahí no
--   hay id de conversación hasta leer el contexto, y una identidad sigue
--   siendo una conversación por organización: única por (organización,
--   wa_identity), exactamente como en 004.
--
-- Los dos índices son parciales para que una regla no estorbe a la otra: una
-- fila por identidad puede tener `crm_conversation_id` repetido (lo aprende
-- del contexto) y las filas por conversación repiten identidad a propósito.
--
-- ## Sin migrar datos
--
-- Las filas existentes quedan como 'identidad', que es lo que eran. Una Nea en
-- modo despacho empieza de cero cada conversación la primera vez tras el
-- cambio (no hay instalaciones de Nagai en producción); el webhook directo no
-- nota nada. Idempotente.

ALTER TABLE bot_conversation
  ADD COLUMN IF NOT EXISTS clave_por TEXT NOT NULL DEFAULT 'identidad';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'bot_conversation_clave_por_check'
  ) THEN
    ALTER TABLE bot_conversation
      ADD CONSTRAINT bot_conversation_clave_por_check
      CHECK (clave_por IN ('identidad', 'conversacion'));
  END IF;
  -- Una fila por conversación siempre sabe de qué conversación del CRM es.
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'bot_conversation_clave_crm_check'
  ) THEN
    ALTER TABLE bot_conversation
      ADD CONSTRAINT bot_conversation_clave_crm_check
      CHECK (clave_por = 'identidad' OR crm_conversation_id IS NOT NULL);
  END IF;
END $$;

-- La unicidad de 004 pasa a valer solo para el webhook directo.
DROP INDEX IF EXISTS uq_bot_conversation_org_identity;

CREATE UNIQUE INDEX IF NOT EXISTS uq_bot_conversation_org_identidad
  ON bot_conversation (organization_id, wa_identity)
  WHERE clave_por = 'identidad';

CREATE UNIQUE INDEX IF NOT EXISTS uq_bot_conversation_org_crm
  ON bot_conversation (organization_id, crm_conversation_id)
  WHERE clave_por = 'conversacion';
