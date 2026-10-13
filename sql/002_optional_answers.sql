SET search_path=research,pg_catalog;
CREATE TABLE evidence(question_id text NOT NULL REFERENCES objects,answer_id text NOT NULL REFERENCES objects,locator text NOT NULL,verification text NOT NULL DEFAULT 'community_unverified' CHECK(verification='community_unverified'),PRIMARY KEY(question_id,answer_id));
CREATE VIEW safe_answers AS
SELECT DISTINCT ON (e.question_id,a.content_hash) e.question_id,a.id answer_id,a.canonical_url,a.safe_snippet answer,e.locator,e.verification,a.published_at,a.updated_at,a.observed_at,a.version
FROM evidence e JOIN objects a ON a.id=e.answer_id JOIN objects q ON q.id=e.question_id
WHERE q.kind='post' AND q.eligible AND a.kind='comment' AND a.eligible AND NOT q.deleted AND NOT a.deleted AND NOT q.unsafe AND NOT a.unsafe AND q.expires_at>now() AND a.expires_at>now() ORDER BY e.question_id,a.content_hash,a.id;
CREATE OR REPLACE VIEW safe_statistics AS SELECT (SELECT count(*) FROM safe_questions) qualified_questions,(SELECT count(*) FROM errors WHERE resolved_at IS NULL) unresolved_errors,(SELECT count(*) FROM objects WHERE unsafe AND safe_snippet<>'' AND NOT deleted) unsafe_backlog,(SELECT count(*) FROM safe_answers) answer_count,(SELECT count(DISTINCT a.question_id) FROM safe_answers a JOIN safe_questions q USING(question_id)) questions_with_answers;
GRANT SELECT,INSERT,UPDATE,DELETE ON evidence TO ctr_writer;
GRANT SELECT ON safe_answers TO ctr_reader;
INSERT INTO schema_version VALUES(2);
