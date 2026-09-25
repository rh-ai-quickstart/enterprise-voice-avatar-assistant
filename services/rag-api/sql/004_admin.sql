-- Admin portal (docs/admin-portal.md): the activity feed, the audit log, transcript archive
-- records, knowledge-gap resolution and full-text search over transcripts.

-- What happened, for the portal's activity feed; Slack is an optional copy of some of these.
CREATE TABLE IF NOT EXISTS activity_events (
  id         BIGSERIAL PRIMARY KEY,
  kind       TEXT NOT NULL,                  -- ticket.approved, document.ingested, integration.error, ...
  severity   TEXT NOT NULL DEFAULT 'info',   -- info | success | warning | error
  title      TEXT NOT NULL,
  detail     TEXT,
  ref_type   TEXT,                           -- ticket | conversation | document | gap | integration
  ref_id     TEXT,
  actor      TEXT,
  source     TEXT NOT NULL,                  -- rag-api | n8n | portal
  data       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS activity_events_created_idx ON activity_events (id DESC);
CREATE INDEX IF NOT EXISTS activity_events_ref_idx ON activity_events (ref_type, ref_id);

-- Every insert wakes the portal's event streams (LISTEN admin_events in each RAG API replica).
CREATE OR REPLACE FUNCTION activity_events_notify() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('admin_events', json_build_object(
    'id', NEW.id, 'kind', NEW.kind, 'ref_type', NEW.ref_type, 'ref_id', NEW.ref_id)::text);
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS activity_events_notify ON activity_events;
CREATE TRIGGER activity_events_notify AFTER INSERT ON activity_events
  FOR EACH ROW EXECUTE FUNCTION activity_events_notify();

-- Who did what in the portal.
CREATE TABLE IF NOT EXISTS admin_audit (
  id          BIGSERIAL PRIMARY KEY,
  actor       TEXT NOT NULL,
  action      TEXT NOT NULL,
  target_type TEXT,
  target_id   TEXT,
  before      JSONB,
  after       JSONB,
  client_ip   TEXT,
  user_agent  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS admin_audit_created_idx ON admin_audit (id DESC);

-- One row per archive of a conversation, whether or not Google Docs is on.
CREATE TABLE IF NOT EXISTS transcript_archives (
  id           BIGSERIAL PRIMARY KEY,
  session_id   TEXT NOT NULL,
  title        TEXT NOT NULL,
  doc_url      TEXT,                          -- Google Doc, when Docs is on and Drive accepted it
  object_key   TEXT,                          -- in the transcripts bucket, set by WF5
  status       TEXT NOT NULL DEFAULT 'requested',  -- requested | indexed | failed
  requested_by TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS transcript_archives_session_idx ON transcript_archives (session_id, id);

-- Knowledge gaps can be resolved or dismissed, and grouped by meaning.
ALTER TABLE knowledge_gaps ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'open';
ALTER TABLE knowledge_gaps ADD COLUMN IF NOT EXISTS resolved_by TEXT;
ALTER TABLE knowledge_gaps ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE knowledge_gaps ADD COLUMN IF NOT EXISTS resolution_note TEXT;
ALTER TABLE knowledge_gaps ADD COLUMN IF NOT EXISTS embedding REAL[];

-- Full-text search over transcripts.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
CREATE INDEX IF NOT EXISTS messages_tsv_idx ON messages USING GIN (tsv);

CREATE INDEX IF NOT EXISTS tickets_status_idx ON tickets (status, id DESC);
CREATE INDEX IF NOT EXISTS tickets_session_idx ON tickets (session_id);
