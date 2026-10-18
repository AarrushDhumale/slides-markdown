-- Level "dockey" is now "donkey"; new level "notes" (short lecture notes condensed from the learner doc).
alter table public.documents drop constraint if exists documents_level_check;
update public.documents set level = 'donkey' where level = 'dockey';
alter table public.documents add constraint documents_level_check
  check (level in ('proficient', 'learner', 'donkey', 'notes', 'summary', 'quiz'));
update public.jobs set levels = array_replace(levels, 'dockey', 'donkey') where 'dockey' = any(levels);
