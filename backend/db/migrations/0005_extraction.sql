-- 0005_extraction.sql
--
-- Review flags on extractions, and one human decision per extraction.

begin;

alter table caseward.extraction
  add column flags jsonb not null default '[]' check (jsonb_typeof(flags) = 'array');

alter table caseward.extraction_review
  add constraint extraction_review_once unique (extraction_id);

commit;
