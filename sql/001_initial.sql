CREATE SCHEMA research AUTHORIZATION ctr_owner;
SET search_path=research,pg_catalog;
CREATE TABLE schema_version(version int PRIMARY KEY);
INSERT INTO schema_version VALUES(1);
CREATE TABLE sources(id text PRIMARY KEY, profile jsonb NOT NULL, cursor text, health text NOT NULL DEFAULT 'blocked', no_new int NOT NULL DEFAULT 0);
CREATE TABLE runs(id uuid PRIMARY KEY, source_id text REFERENCES sources, stage text NOT NULL, fingerprint text NOT NULL, status text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz, summary jsonb);
CREATE TABLE host_limits(host text PRIMARY KEY, next_at timestamptz NOT NULL);
CREATE TABLE requests(id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES runs, url text NOT NULL, status int, outcome text NOT NULL, attempted_at timestamptz NOT NULL, elapsed_ms int, mime text, bytes int, attempt int NOT NULL, final_url text NOT NULL);
CREATE TABLE archives(hash text PRIMARY KEY, path text, created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, purged_at timestamptz);
CREATE TABLE request_archives(request_id uuid PRIMARY KEY REFERENCES requests, hash text NOT NULL REFERENCES archives);
CREATE TABLE archive_members(request_id uuid NOT NULL REFERENCES requests,object_id text NOT NULL,PRIMARY KEY(request_id,object_id));
CREATE TABLE errors(external_id text PRIMARY KEY, source_id text NOT NULL REFERENCES sources, url text NOT NULL, kind text NOT NULL, terminal boolean NOT NULL, first_at timestamptz NOT NULL, last_at timestamptz NOT NULL, retry_count int NOT NULL DEFAULT 0, latest_request uuid REFERENCES requests, resolved_at timestamptz, resolution_request uuid REFERENCES requests);
CREATE TABLE frontier(post_id text PRIMARY KEY, source_id text NOT NULL REFERENCES sources, state text NOT NULL DEFAULT 'pending', discovered_run uuid NOT NULL REFERENCES runs, observed_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE manifests(id text PRIMARY KEY, run_id uuid NOT NULL REFERENCES runs, request_id uuid NOT NULL REFERENCES requests, archive_hash text NOT NULL REFERENCES archives, stage text NOT NULL, schema_version int NOT NULL, parser_version text NOT NULL, cleaner_version text NOT NULL, input_hash text NOT NULL, output_hash text NOT NULL, path text, payload_path text, imported_at timestamptz, cursor int NOT NULL DEFAULT 0, purged_at timestamptz);
CREATE TABLE objects(id text PRIMARY KEY, source_id text NOT NULL REFERENCES sources, kind text NOT NULL CHECK(kind IN ('post','comment')), post_id text NOT NULL, parent_id text, canonical_url text, published_at timestamptz NOT NULL, updated_at timestamptz, observed_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, version int NOT NULL, content_hash text, deleted boolean NOT NULL DEFAULT false, unsafe boolean NOT NULL, topic text, eligible boolean NOT NULL DEFAULT false, safe_snippet text CHECK(length(safe_snippet)<=360));
CREATE TABLE versions(object_id text NOT NULL REFERENCES objects, version int NOT NULL, content_hash text, safe_snippet text CHECK(length(safe_snippet)<=360), request_id uuid NOT NULL REFERENCES requests, archive_hash text NOT NULL REFERENCES archives, manifest_id text NOT NULL REFERENCES manifests, observed_at timestamptz NOT NULL, purged_at timestamptz, PRIMARY KEY(object_id,version));
CREATE TABLE tombstones(object_id text PRIMARY KEY, reason text NOT NULL, observed_at timestamptz NOT NULL, run_id uuid REFERENCES runs);
CREATE TABLE deletion_audit(id uuid PRIMARY KEY, object_id text NOT NULL, phase text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), details jsonb NOT NULL);
CREATE TABLE progress(seq bigserial PRIMARY KEY, run_id uuid NOT NULL REFERENCES runs, status text NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now(), summary jsonb NOT NULL);
CREATE VIEW safe_questions AS
SELECT DISTINCT ON (q.content_hash) q.id question_id,q.source_id,q.canonical_url,q.published_at,q.observed_at,q.version,q.topic,q.safe_snippet question
FROM objects q
WHERE q.kind='post' AND q.eligible AND NOT q.deleted AND NOT q.unsafe AND q.expires_at>now()
ORDER BY q.content_hash,q.id;
CREATE VIEW safe_statistics AS SELECT (SELECT count(*) FROM safe_questions) qualified_questions, (SELECT count(*) FROM errors WHERE resolved_at IS NULL) unresolved_errors, (SELECT count(*) FROM objects WHERE unsafe AND NOT deleted) unsafe_backlog;
GRANT USAGE ON SCHEMA research TO ctr_writer,ctr_reader;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA research TO ctr_writer;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA research TO ctr_writer;
GRANT SELECT ON safe_questions,safe_statistics TO ctr_reader;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA research FROM ctr_writer,ctr_reader;
