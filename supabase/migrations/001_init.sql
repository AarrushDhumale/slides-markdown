-- s2m web app schema. Run once in the Supabase SQL editor (or `supabase db push`).
-- Auth: anonymous sign-ins (enable under Authentication -> Providers -> Anonymous). Every browser gets a
-- user id; RLS keeps notebooks private to it. Google sign-in can later be *linked* to the same user.
-- Writes that need checks (jobs, explain requests, uploads) go through the Next.js API routes with the
-- service-role key; the browser itself can only read its own rows and rename/delete its notebooks.

create extension if not exists pgcrypto;

-- ---------- tables ----------

create table public.notebooks (
  id          uuid primary key default gen_random_uuid(),
  owner       uuid not null default auth.uid() references auth.users on delete cascade,
  title       text not null default 'Untitled notebook' check (length(title) <= 200),
  status      text not null default 'empty'
              check (status in ('empty', 'queued', 'processing', 'ready', 'failed')),
  slide_count int  not null default 0,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index on public.notebooks (owner, updated_at desc);

create table public.sources (
  id           uuid primary key default gen_random_uuid(),
  notebook_id  uuid not null references public.notebooks on delete cascade,
  owner        uuid not null references auth.users on delete cascade,
  filename     text not null,
  storage_path text not null unique,
  size_bytes   bigint not null,
  pages        int,
  position     int not null default 0,
  created_at   timestamptz not null default now()
);
create index on public.sources (notebook_id, position);

create table public.jobs (
  id           uuid primary key default gen_random_uuid(),
  notebook_id  uuid not null references public.notebooks on delete cascade,
  owner        uuid not null references auth.users on delete cascade,
  levels       text[] not null,
  status       text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed')),
  stage        text,
  done         int not null default 0,
  total        int not null default 0,
  error        text,
  cost_usd     numeric(10, 6) not null default 0,
  worker       text,
  created_at   timestamptz not null default now(),
  started_at   timestamptz,
  finished_at  timestamptz,
  heartbeat_at timestamptz
);
create index on public.jobs (status, created_at);
create index on public.jobs (owner, created_at desc);
create index on public.jobs (notebook_id, created_at desc);

create table public.documents (
  notebook_id  uuid not null references public.notebooks on delete cascade,
  level        text not null check (level in ('proficient', 'learner', 'dockey', 'summary', 'quiz')),
  storage_path text not null,
  word_count   int not null default 0,
  updated_at   timestamptz not null default now(),
  primary key (notebook_id, level)
);

create table public.worker_status (
  id          text primary key,
  last_seen   timestamptz not null default now(),
  busy        boolean not null default false,
  current_job uuid,
  local_model boolean not null default false,   -- local Ollama up and free for "Explain this"
  month_usd   numeric(10, 4) not null default 0
);

create table public.explain_requests (
  id           uuid primary key default gen_random_uuid(),
  notebook_id  uuid not null references public.notebooks on delete cascade,
  owner        uuid not null references auth.users on delete cascade,
  level        text not null,
  section      int not null,
  selection    text check (length(selection) <= 2000),
  image        text,
  history      jsonb not null default '[]',
  simpler      boolean not null default false,
  cache_key    text,
  status       text not null default 'queued'
               check (status in ('queued', 'running', 'streaming', 'done', 'failed')),
  partial_text text not null default '',
  answer       text,
  model        text,
  source       text,          -- 'local' | 'cloud'
  error        text,
  worker       text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index on public.explain_requests (status, created_at);
create index on public.explain_requests (owner, created_at desc);

create table public.explain_cache (
  key        text primary key,
  answer     text not null,
  model      text,
  source     text,
  created_at timestamptz not null default now()
);

-- ---------- row-level security ----------

alter table public.notebooks        enable row level security;
alter table public.sources          enable row level security;
alter table public.jobs             enable row level security;
alter table public.documents        enable row level security;
alter table public.worker_status    enable row level security;
alter table public.explain_requests enable row level security;
alter table public.explain_cache    enable row level security;   -- no policies: service role only

create policy "own notebooks: read"   on public.notebooks for select to authenticated using (owner = auth.uid());
create policy "own notebooks: rename" on public.notebooks for update to authenticated
  using (owner = auth.uid()) with check (owner = auth.uid());
create policy "own notebooks: delete" on public.notebooks for delete to authenticated using (owner = auth.uid());
-- only the title may change from the browser; status/slide_count are the worker's
revoke update on public.notebooks from authenticated;
grant update (title) on public.notebooks to authenticated;

create policy "own sources: read" on public.sources for select to authenticated using (owner = auth.uid());
create policy "own jobs: read"    on public.jobs    for select to authenticated using (owner = auth.uid());
create policy "own docs: read"    on public.documents for select to authenticated using (
  exists (select 1 from public.notebooks n where n.id = notebook_id and n.owner = auth.uid()));
create policy "own explains: read" on public.explain_requests for select to authenticated using (owner = auth.uid());
create policy "worker status: read" on public.worker_status for select to authenticated using (true);

-- ---------- worker functions (service role only) ----------

-- Claim the oldest queued job; a 'running' job whose worker went silent for 15 min is reclaimed
-- (finished slides/sections are cached, so a reclaimed job only redoes the unfinished calls).
create or replace function public.claim_job(p_worker text)
returns setof public.jobs language plpgsql security definer set search_path = public as $$
declare
  claimed public.jobs;
begin
  update public.jobs j
     set status = 'running', worker = p_worker, stage = 'starting', error = null,
         started_at = coalesce(j.started_at, now()), heartbeat_at = now()
   where j.id = (select id from public.jobs
                  where status = 'queued'
                     or (status = 'running' and heartbeat_at < now() - interval '15 minutes')
                  order by created_at
                  for update skip locked
                  limit 1)
  returning j.* into claimed;
  if claimed.id is not null then
    update public.notebooks set status = 'processing', updated_at = now() where id = claimed.notebook_id;
    return next claimed;
  end if;
end $$;

-- Claim the oldest fresh explain request (older ones were answered by the cloud fallback instead).
create or replace function public.claim_explain(p_worker text)
returns setof public.explain_requests language sql security definer set search_path = public as $$
  update public.explain_requests e
     set status = 'running', worker = p_worker, updated_at = now()
   where e.id = (select id from public.explain_requests
                  where status = 'queued' and created_at > now() - interval '2 minutes'
                  order by created_at
                  for update skip locked
                  limit 1)
  returning e.*;
$$;

revoke execute on function public.claim_job(text)     from public, anon, authenticated;
revoke execute on function public.claim_explain(text) from public, anon, authenticated;

-- ---------- storage ----------

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('pdfs', 'pdfs', false, 52428800, array['application/pdf']),   -- 50 MB per PDF
       ('outputs', 'outputs', false, null, null)
on conflict (id) do nothing;

-- Uploads use signed upload URLs minted by /api/uploads (after the size/quota checks), so the browser
-- needs no insert policy. Generated files live under outputs/<notebook id>/…; owners may read them.
create policy "own outputs: read" on storage.objects for select to authenticated using (
  bucket_id = 'outputs'
  and exists (select 1 from public.notebooks n
               where n.id::text = (storage.foldername(name))[1] and n.owner = auth.uid()));

-- ---------- realtime ----------

alter publication supabase_realtime add table public.jobs, public.documents, public.notebooks,
  public.explain_requests, public.worker_status;
