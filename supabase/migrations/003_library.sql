-- Shared chapter library, per-section level choices and user notes.
-- A "chapter" is one PDF, identified by its content hash (sha). Generated files are stored once per chapter
-- (bucket `library`, folder = chapter slug) and reused by every notebook that contains the same PDF.
-- `documents` (per-notebook docs from 001/002) is no longer written; it is left in place.

-- ---------- tables ----------

create table public.chapters (
  id             uuid primary key default gen_random_uuid(),
  sha            text not null unique check (sha ~ '^[0-9a-f]{16}$'),   -- same as backend ingest.file_sha
  slug           text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{0,100}$'),
  title          text not null,
  filename       text not null,
  pages          int,
  status         text not null default 'new' check (status in ('new', 'generating', 'ready', 'failed')),
  generating_job uuid,
  heartbeat_at   timestamptz,
  error          text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create table public.chapter_docs (
  chapter_id   uuid not null references public.chapters on delete cascade,
  level        text not null check (level in ('proficient', 'learner', 'donkey', 'notes', 'summary', 'quiz')),
  storage_path text not null,
  word_count   int not null default 0,
  sections     int not null default 0,
  model        text,
  updated_at   timestamptz not null default now(),
  primary key (chapter_id, level)
);

alter table public.sources add column sha text;
alter table public.sources add column chapter_id uuid references public.chapters;
create index on public.sources (chapter_id);

alter table public.jobs add column chapter_id uuid references public.chapters;   -- chapter being generated now
alter table public.jobs add column chapters_done int not null default 0;
alter table public.jobs add column chapters_total int not null default 0;

alter table public.explain_requests add column chapter_id uuid references public.chapters;

-- a rewrite of one section at another level; shared because the chapter content is shared
create table public.section_variants (
  chapter_id uuid not null references public.chapters on delete cascade,
  section    int  not null check (section >= 0),
  level      text not null check (level in ('proficient', 'learner', 'donkey')),
  body       text not null,
  model      text,
  cost_usd   numeric(10, 6) not null default 0,
  created_by uuid references auth.users on delete set null,   -- who paid for it (rewrite quota)
  created_at timestamptz not null default now(),
  primary key (chapter_id, section, level)
);

-- which level this notebook shows for a section (absent = the document default)
create table public.section_choices (
  notebook_id uuid not null references public.notebooks on delete cascade,
  chapter_id  uuid not null references public.chapters on delete cascade,
  section     int  not null check (section >= 0),
  level       text not null check (level in ('proficient', 'learner', 'donkey')),
  updated_at  timestamptz not null default now(),
  primary key (notebook_id, chapter_id, section)
);

create table public.user_notes (
  notebook_id uuid not null references public.notebooks on delete cascade,
  chapter_id  uuid not null references public.chapters on delete cascade,
  section     int  not null check (section >= 0),
  owner       uuid not null default auth.uid() references auth.users on delete cascade,
  body        text not null default '' check (length(body) <= 20000),
  updated_at  timestamptz not null default now(),
  primary key (notebook_id, chapter_id, section)
);
create index on public.user_notes (owner, updated_at desc);

-- ---------- row-level security ----------

alter table public.chapters         enable row level security;
alter table public.chapter_docs     enable row level security;
alter table public.section_variants enable row level security;
alter table public.section_choices  enable row level security;
alter table public.user_notes       enable row level security;

-- a chapter is visible to anyone who has it in one of their notebooks
create or replace function public.owns_chapter(p_chapter uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.sources s where s.chapter_id = p_chapter and s.owner = auth.uid());
$$;

create policy "own chapters: read" on public.chapters for select to authenticated
  using (public.owns_chapter(id));
create policy "own chapter docs: read" on public.chapter_docs for select to authenticated
  using (public.owns_chapter(chapter_id));
create policy "own variants: read" on public.section_variants for select to authenticated
  using (public.owns_chapter(chapter_id));

create policy "own choices: read" on public.section_choices for select to authenticated
  using (exists (select 1 from public.notebooks n where n.id = notebook_id and n.owner = auth.uid()));
-- choices are written by /api/sections/rewrite (service role), after the quota checks

create policy "own notes: read" on public.user_notes for select to authenticated using (owner = auth.uid());
create policy "own notes: insert" on public.user_notes for insert to authenticated
  with check (owner = auth.uid()
    and exists (select 1 from public.notebooks n where n.id = notebook_id and n.owner = auth.uid())
    and public.owns_chapter(chapter_id));
create policy "own notes: update" on public.user_notes for update to authenticated
  using (owner = auth.uid()) with check (owner = auth.uid());
create policy "own notes: delete" on public.user_notes for delete to authenticated using (owner = auth.uid());

-- ---------- worker function ----------

-- Claim a chapter for generation. Returns true if this job now owns it; false if another live job does
-- (a 'generating' chapter whose heartbeat went silent for 15 min is taken over, like claim_job).
create or replace function public.claim_chapter(p_chapter uuid, p_job uuid)
returns boolean language plpgsql security definer set search_path = public as $$
declare
  n int;
begin
  update public.chapters
     set status = 'generating', generating_job = p_job, heartbeat_at = now(), error = null, updated_at = now()
   where id = p_chapter
     and (status <> 'generating' or generating_job = p_job or heartbeat_at is null
          or heartbeat_at < now() - interval '15 minutes');
  get diagnostics n = row_count;
  return n > 0;
end $$;
revoke execute on function public.claim_chapter(uuid, uuid) from public, anon, authenticated;

-- ---------- storage ----------

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('library', 'library', false, null, null)
on conflict (id) do nothing;

create policy "own library: read" on storage.objects for select to authenticated using (
  bucket_id = 'library'
  and exists (select 1 from public.chapters c
               where c.slug = (storage.foldername(name))[1] and public.owns_chapter(c.id)));

-- ---------- realtime ----------

alter publication supabase_realtime add table public.chapters, public.chapter_docs,
  public.section_variants, public.section_choices;
