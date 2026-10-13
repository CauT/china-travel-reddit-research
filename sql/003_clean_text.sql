SET search_path=research,pg_catalog;
ALTER TABLE objects ADD COLUMN clean_text text CHECK(length(clean_text)<=50000);
ALTER TABLE versions ADD COLUMN clean_text text CHECK(length(clean_text)<=50000);
INSERT INTO schema_version VALUES(3);
