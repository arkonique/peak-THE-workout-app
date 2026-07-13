-- Additive storage for user-owned Gemini conversation pointers.
-- Message content remains in Google's Interactions API and is not copied here.

create table if not exists public.ai_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  title text not null check (length(trim(title)) between 1 and 120),
  latest_provider_interaction_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, user_id)
);

create index if not exists ai_sessions_user_updated_idx
  on public.ai_sessions (user_id, updated_at desc);

alter table public.ai_sessions enable row level security;

revoke all on table public.ai_sessions from anon;
grant select, insert, update, delete on table public.ai_sessions to authenticated;
grant all on table public.ai_sessions to service_role;

drop policy if exists ai_sessions_owner_all on public.ai_sessions;
create policy ai_sessions_owner_all
  on public.ai_sessions for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

notify pgrst, 'reload schema';
