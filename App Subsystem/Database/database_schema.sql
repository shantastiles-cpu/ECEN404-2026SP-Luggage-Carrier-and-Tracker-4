create table if not exists telemetry_snapshots (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  -- optional foreign key to sessions table if you want
  session_id uuid,

  -- what the Flutter UI needs
  distance_meters double precision,
  weight_kg double precision,
  obstacle_hold boolean default false,
  arrived boolean default false
);

insert into telemetry_snapshots (distance_meters, weight_kg, obstacle_hold, arrived)
values (1.8, 6.0, false, false);



-- 1) Rovers (hardware units)
create table if not exists rovers (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  hw_serial text,               -- optional: your Pi / MCU serial
  created_at timestamptz default now()
);

-- 2) Phone app users (optional, but nice)
create table if not exists app_users (
  id uuid primary key default gen_random_uuid(),
  platform text,                -- 'android', 'ios'
  device_model text,
  created_at timestamptz default now()
);

-- 3) Follow sessions (one per "tracking on" run)
create table if not exists rover_sessions (
  id uuid primary key default gen_random_uuid(),
  rover_id uuid references rovers(id),
  app_user_id uuid references app_users(id),
  start_time timestamptz default now(),
  end_time timestamptz,
  start_floor text,             -- e.g. 'L1'
  end_floor text,
  start_notes text,
  end_notes text
);

-- 4) Time-series poses (user + rover)
create table if not exists pose_samples (
  id bigserial primary key,
  session_id uuid references rover_sessions(id) on delete cascade,
  ts timestamptz default now(),
  floor_id text not null,       -- 'L1'...'L5'
  user_x_m double precision,
  user_y_m double precision,
  rover_x_m double precision,
  rover_y_m double precision,
  distance_m double precision,
  follow_distance_m double precision,
  max_separation_m double precision
);

-- 5) Events (alerts, obstacles, cargo, etc.)
create table if not exists events (
  id bigserial primary key,
  session_id uuid references rover_sessions(id) on delete cascade,
  ts timestamptz default now(),
  event_type text not null,     -- 'SEPARATION', 'OBSTACLE', 'LOAD_CHANGE', etc.
  floor_id text,
  x_m double precision,
  y_m double precision,
  payload jsonb                 -- extra data (old_weight, new_weight, etc.)
);


insert into pose_samples (floor_id, user_x_m, user_y_m)
values ('L1', 80.0, 100.0);

select *
from pose_samples
order by ts desc
limit 10;

-- Make sure at least one row exists
insert into telemetry_snapshots (distance_meters, weight_kg, obstacle_hold, arrived)
values (1.5, 6.0, false, false);

-- Then, update it and watch app change
update telemetry_snapshots
set distance_meters = 3.0,
    weight_kg = 7.0,
    obstacle_hold = true
where id = (
  select id from telemetry_snapshots
  order by created_at desc
  limit 1
);


update telemetry_snapshots
set distance_meters = 0.2
where id = (
  select id from telemetry_snapshots
  order by created_at desc
  limit 1
);


insert into pose_samples (floor_id, user_x_m, user_y_m)
values ('L3', 102.0, 90.0);


-- Clear pose_samples
TRUNCATE TABLE public.pose_samples
RESTART IDENTITY
CASCADE;

-- Clear telemetry_snapshots
TRUNCATE TABLE public.telemetry_snapshots
RESTART IDENTITY
CASCADE;

----------- ECEN 404 --------------

create table robot_telemetry (
  id uuid primary key default gen_random_uuid(),
  robot_id text not null,
  created_at timestamptz default now(),

  front_cm  real,
  right_cm  real,
  left_cm   real,
  back_cm   real,

  avoid_active boolean,
  avoid_reason text,
  motor_state  text,

  weight_g     real,
  weight_valid boolean,

  raw jsonb
);


create table robot_state (
  robot_id text primary key,
  updated_at timestamptz default now(),

  front_cm  real,
  right_cm  real,
  left_cm   real,
  back_cm   real,

  avoid_active boolean,
  avoid_reason text,
  motor_state  text,

  weight_g     real,
  weight_valid boolean,

  raw jsonb
);

create index on robot_telemetry (robot_id, created_at desc);


create table if not exists nav_state (
  robot_id text primary key,
  updated_at timestamptz default now(),

  -- sensor/state coming from this demo
  distance_cm real,
  heading_deg real,
  luggage_fallen boolean,

  -- controller outputs
  left_speed_cmd real,
  right_speed_cmd real,

  -- safety status
  safety_active boolean,
  safety_msg text,

  -- optional debug/meta
  controller text,
  raw jsonb
);


create table if not exists nav_telemetry (
  id uuid primary key default gen_random_uuid(),
  robot_id text not null,
  created_at timestamptz default now(),

  distance_cm real,
  heading_deg real,
  luggage_fallen boolean,

  left_speed_cmd real,
  right_speed_cmd real,

  safety_active boolean,
  safety_msg text,

  controller text,
  raw jsonb
);

create index if not exists nav_telemetry_robot_time
on nav_telemetry (robot_id, created_at desc);


alter table nav_state enable row level security;

create policy "read nav_state for authenticated"
on nav_state
for select
to authenticated
using (true);



///////////////

-- Table for current navigation state (written by rover)
CREATE TABLE IF NOT EXISTS nav_state (
  robot_id TEXT PRIMARY KEY,
  distance_cm REAL,
  heading_deg REAL,
  luggage_fallen BOOLEAN,
  left_speed_cmd REAL,
  right_speed_cmd REAL,
  safety_active BOOLEAN,
  safety_msg TEXT,
  controller TEXT,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Table for telemetry history (written by rover)
CREATE TABLE IF NOT EXISTS nav_telemetry (
  id BIGSERIAL PRIMARY KEY,
  robot_id TEXT NOT NULL,
  distance_cm REAL,
  heading_deg REAL,
  luggage_fallen BOOLEAN,
  left_speed_cmd REAL,
  right_speed_cmd REAL,
  safety_active BOOLEAN,
  safety_msg TEXT,
  controller TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- NEW: Table for remote commands (read by rover, written by web UI)
CREATE TABLE IF NOT EXISTS rover_commands (
  robot_id TEXT PRIMARY KEY,
  mode TEXT DEFAULT 'auto', -- 'auto', 'manual', 'stop'
  manual_left_speed REAL DEFAULT 0,
  manual_right_speed REAL DEFAULT 0,
  target_distance REAL DEFAULT 35.0,
  target_heading REAL DEFAULT 0.0,
  emergency_stop BOOLEAN DEFAULT FALSE,
  reset_luggage BOOLEAN DEFAULT FALSE,
  command_updated_at TIMESTAMPTZ DEFAULT NOW(),
  command_version INTEGER DEFAULT 0 -- Increment to detect changes
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_nav_telemetry_robot_time 
  ON nav_telemetry(robot_id, created_at DESC);

-- Enable Row Level Security (optional but recommended)
ALTER TABLE nav_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE nav_telemetry ENABLE ROW LEVEL SECURITY;
ALTER TABLE rover_commands ENABLE ROW LEVEL SECURITY;

-- Policies (allow service role full access)
CREATE POLICY "Enable all for service role" ON nav_state
  FOR ALL USING (auth.role() = 'service_role');

CREATE POLICY "Enable all for service role" ON nav_telemetry
  FOR ALL USING (auth.role() = 'service_role');

CREATE POLICY "Enable all for service role" ON rover_commands
  FOR ALL USING (auth.role() = 'service_role');

-- Insert default command record for your rover
INSERT INTO rover_commands (robot_id, mode)
VALUES ('rover_01', 'auto')
ON CONFLICT (robot_id) DO NOTHING;



INSERT INTO rover_commands (
  robot_id, 
  mode, 
  manual_left_speed, 
  manual_right_speed, 
  target_distance, 
  target_heading, 
  emergency_stop, 
  reset_luggage,
  command_version
) VALUES (
  'rover_01', 
  'auto', 
  0.0, 
  0.0, 
  35.0, 
  0.0, 
  false, 
  false,
  0
)
ON CONFLICT (robot_id) DO UPDATE SET
  command_version = 0;


-- Allow anonymous users to insert/update rover_commands
CREATE POLICY "Allow anon insert/update rover_commands" 
ON rover_commands
FOR ALL 
TO anon
USING (true)
WITH CHECK (true);


-- Allow anon users to read current nav state
CREATE POLICY "Allow anon read nav_state"
ON nav_state
FOR SELECT
TO anon
USING (true);

-- (Optional) allow anon users to read telemetry history
CREATE POLICY "Allow anon read nav_telemetry"
ON nav_telemetry
FOR SELECT
TO anon
USING (true);

select * from nav_state where robot_id = 'rover_01';


create table if not exists nav_state (
  robot_id text primary key,

  -- high-level state
  mode text not null default 'auto',

  -- what the app/command layer requested
  left_speed_cmd  double precision not null default 0,
  right_speed_cmd double precision not null default 0,

  -- what the rover actually applied (optional but recommended)
  left_speed_applied  double precision,
  right_speed_applied double precision,

  -- optional: last command version applied (nice for debugging/ack)
  command_version_applied bigint,

  updated_at timestamptz not null default now()
);

create index if not exists nav_state_updated_idx
on nav_state(updated_at desc);



create or replace function public.sync_rover_commands_to_nav_state()
returns trigger
language plpgsql
as $$
begin
  insert into public.nav_state as ns (
    robot_id,
    mode,
    left_speed_cmd,
    right_speed_cmd,
    updated_at
  )
  values (
    new.robot_id,
    coalesce(new.mode, 'auto'),
    coalesce(new.manual_left_speed, 0),
    coalesce(new.manual_right_speed, 0),
    now()
  )
  on conflict (robot_id) do update
  set
    mode = excluded.mode,
    left_speed_cmd = excluded.left_speed_cmd,
    right_speed_cmd = excluded.right_speed_cmd,
    updated_at = excluded.updated_at;

  return new;
end;
$$;


drop trigger if exists trg_sync_rover_commands_to_nav_state on public.rover_commands;

create trigger trg_sync_rover_commands_to_nav_state
after insert or update of mode, manual_left_speed, manual_right_speed, command_version
on public.rover_commands
for each row
execute function public.sync_rover_commands_to_nav_state();

ALTER TABLE nav_state
ADD COLUMN left_speed_applied REAL,
ADD COLUMN right_speed_applied REAL,
ADD COLUMN command_version_applied INTEGER;


UPDATE nav_state
SET
  left_speed_applied = 45.0,
  right_speed_applied = 45.0,
  command_version_applied = 3,
  updated_at = NOW()
WHERE robot_id = 'rover_01';


alter table nav_state enable row level security;

drop policy if exists "read nav_state" on nav_state;
create policy "read nav_state"
on nav_state for select
using (true);



create or replace function public.sync_rover_commands_to_nav_state()
returns trigger
language plpgsql
as $$
begin
  insert into public.nav_state as ns (
    robot_id,
    left_speed_cmd,
    right_speed_cmd,
    updated_at
  )
  values (
    new.robot_id,
    coalesce(new.manual_left_speed, 0),
    coalesce(new.manual_right_speed, 0),
    now()
  )
  on conflict (robot_id) do update
  set
    left_speed_cmd  = excluded.left_speed_cmd,
    right_speed_cmd = excluded.right_speed_cmd,
    updated_at      = excluded.updated_at;

  return new;
end;
$$;


drop trigger if exists trg_sync_rover_commands_to_nav_state on public.rover_commands;

create trigger trg_sync_rover_commands_to_nav_state
after insert or update of manual_left_speed, manual_right_speed, mode, command_version
on public.rover_commands
for each row
execute function public.sync_rover_commands_to_nav_state();


alter table public.nav_state
add constraint nav_state_robot_id_key unique (robot_id);


create or replace function public.sync_nav_state_from_rover_commands()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.nav_state (
    robot_id,
    left_speed_cmd,
    right_speed_cmd,
    command_version_cmd,
    updated_at
  )
  values (
    new.robot_id,
    new.manual_left_speed,
    new.manual_right_speed,
    new.command_version,
    now()
  )
  on conflict (robot_id)
  do update set
    left_speed_cmd = excluded.left_speed_cmd,
    right_speed_cmd = excluded.right_speed_cmd,
    command_version_cmd = excluded.command_version_cmd,
    updated_at = now();

  return new;
end;
$$;



drop trigger if exists trg_sync_nav_state_from_rover_commands on public.rover_commands;

create trigger trg_sync_nav_state_from_rover_commands
after insert or update on public.rover_commands
for each row
execute function public.sync_nav_state_from_rover_commands();


alter table public.nav_state enable row level security;

drop policy if exists "read nav_state" on public.nav_state;

create policy "read nav_state"
on public.nav_state
for select
using (true);

alter table public.rover_commands enable row level security;

drop policy if exists "rw rover_commands" on public.rover_commands;


create policy "rw rover_commands"
on public.rover_commands
for all
using (true)
with check (true);


alter table public.nav_state
add column if not exists command_version_cmd integer;


alter table public.nav_state enable row level security;

drop policy if exists "read nav_state" on public.nav_state;

create policy "read nav_state"
on public.nav_state
for select
using (true);


create or replace function public.sync_nav_state_from_rover_commands()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.nav_state (robot_id, left_speed_cmd, right_speed_cmd, updated_at)
  values (new.robot_id, new.manual_left_speed, new.manual_right_speed, now())
  on conflict (robot_id) do update set
    left_speed_cmd = excluded.left_speed_cmd,
    right_speed_cmd = excluded.right_speed_cmd,
    updated_at = now();

  return new;
end;
$$;


drop trigger if exists trg_sync_nav_state_from_rover_commands on public.rover_commands;

create trigger trg_sync_nav_state_from_rover_commands
after insert or update on public.rover_commands
for each row
execute function public.sync_nav_state_from_rover_commands();




-- 1) Ensure RLS is enabled (fine if already enabled)
alter table public.nav_state enable row level security;

-- 2) Keep ONLY a read policy for everyone (anon/authenticated)
drop policy if exists "read nav_state" on public.nav_state;
create policy "read nav_state"
on public.nav_state
for select
using (true);

-- 3) Create write policies ONLY for postgres (so clients still can't write)
drop policy if exists "nav_state_write_postgres" on public.nav_state;
create policy "nav_state_write_postgres"
on public.nav_state
for insert
to postgres
with check (true);

drop policy if exists "nav_state_update_postgres" on public.nav_state;
create policy "nav_state_update_postgres"
on public.nav_state
for update
to postgres
using (true)
with check (true);

-- 4) Recreate the trigger function (security definer) and make sure it's owned by postgres
create or replace function public.sync_nav_state_from_rover_commands()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.nav_state (robot_id, left_speed_cmd, right_speed_cmd, updated_at)
  values (new.robot_id, coalesce(new.manual_left_speed,0), coalesce(new.manual_right_speed,0), now())
  on conflict (robot_id) do update set
    left_speed_cmd = excluded.left_speed_cmd,
    right_speed_cmd = excluded.right_speed_cmd,
    updated_at = now();

  return new;
end;
$$;

-- IMPORTANT: make postgres the owner so the SECURITY DEFINER runs as postgres
alter function public.sync_nav_state_from_rover_commands() owner to postgres;

-- 5) Recreate trigger
drop trigger if exists trg_sync_nav_state_from_rover_commands on public.rover_commands;

create trigger trg_sync_nav_state_from_rover_commands
after insert or update on public.rover_commands
for each row
execute function public.sync_nav_state_from_rover_commands();



update public.rover_commands
set manual_left_speed = 12,
    manual_right_speed = 34,
    command_version = command_version + 1,
    command_updated_at = now()
where robot_id = 'rover_01';

select * from public.nav_state where robot_id = 'rover_01';



select n.nspname as schema,
       p.proname as function,
       pg_get_userbyid(p.proowner) as owner,
       p.prosecdef as security_definer
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where p.proname = 'sync_nav_state_from_rover_commands';



-- Make sure RLS is on (it is)
alter table public.rover_commands enable row level security;

-- Allow anon/users to SELECT the rover row (optional but recommended)
drop policy if exists "Allow select rover_commands" on public.rover_commands;
create policy "Allow select rover_commands"
on public.rover_commands
for select
using (true);

-- Allow anon/users to UPDATE rover_commands (demo-wide open)
drop policy if exists "Allow update rover_commands" on public.rover_commands;
create policy "Allow update rover_commands"
on public.rover_commands
for update
using (true)
with check (true);



-- 1) Recreate trigger function to run with definer privileges
create or replace function public.sync_nav_state_from_rover_commands()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  -- Upsert into nav_state based on rover_commands
  insert into public.nav_state (
    robot_id,
    left_speed_cmd,
    right_speed_cmd,
    mode,
    updated_at
  )
  values (
    new.robot_id,
    coalesce(new.manual_left_speed, 0),
    coalesce(new.manual_right_speed, 0),
    coalesce(new.mode, 'auto'),
    now()
  )
  on conflict (robot_id) do update
  set
    left_speed_cmd  = excluded.left_speed_cmd,
    right_speed_cmd = excluded.right_speed_cmd,
    mode            = excluded.mode,
    updated_at      = excluded.updated_at;

  return new;
end;
$$;

-- 2) Ensure the function is owned by postgres (important for bypassing RLS)
alter function public.sync_nav_state_from_rover_commands() owner to postgres;



select column_name, data_type
from information_schema.columns
where table_schema = 'public'
  and table_name = 'nav_state'
order by ordinal_position;


SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'nav_state'
ORDER BY ordinal_position;



create or replace function public.sync_nav_state_from_rover_commands()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.nav_state (
    robot_id,
    left_speed_cmd,
    right_speed_cmd,
    updated_at
  )
  values (
    new.robot_id,
    coalesce(new.manual_left_speed, 0),
    coalesce(new.manual_right_speed, 0),
    now()
  )
  on conflict (robot_id) do update
  set
    left_speed_cmd  = excluded.left_speed_cmd,
    right_speed_cmd = excluded.right_speed_cmd,
    updated_at      = excluded.updated_at;

  return new;
end;
$$;

alter function public.sync_nav_state_from_rover_commands() owner to postgres;



-- 1) Make nav_state readable but not writable from client roles
REVOKE INSERT, UPDATE, DELETE ON public.nav_state FROM anon, authenticated;
GRANT  SELECT               ON public.nav_state TO   anon, authenticated;

-- 2) (Optional) keep RLS enabled for select policies if you want,
-- but the REVOKE above already blocks client writes.




BEGIN;

DROP TRIGGER IF EXISTS trg_sync_nav_state_from_rover_commands ON public.rover_commands;
DROP FUNCTION IF EXISTS public.sync_nav_state_from_rover_commands();

CREATE FUNCTION public.sync_nav_state_from_rover_commands()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.nav_state (
    robot_id,
    left_speed_cmd,
    right_speed_cmd,
    updated_at
  )
  VALUES (
    NEW.robot_id,
    COALESCE(NEW.manual_left_speed, 0),
    COALESCE(NEW.manual_right_speed, 0),
    NOW()
  )
  ON CONFLICT (robot_id) DO UPDATE
    SET left_speed_cmd  = EXCLUDED.left_speed_cmd,
        right_speed_cmd = EXCLUDED.right_speed_cmd,
        updated_at      = EXCLUDED.updated_at;

  RETURN NEW;
END;
$$;

ALTER FUNCTION public.sync_nav_state_from_rover_commands() OWNER TO postgres;

CREATE TRIGGER trg_sync_nav_state_from_rover_commands
AFTER INSERT OR UPDATE OF manual_left_speed, manual_right_speed ON public.rover_commands
FOR EACH ROW
EXECUTE FUNCTION public.sync_nav_state_from_rover_commands();

COMMIT;


GRANT SELECT ON public.nav_state TO anon, authenticated;



SELECT relrowsecurity, relforcerowsecurity
FROM pg_class
WHERE relname = 'nav_state';



CREATE POLICY "read nav_state"
ON public.nav_state
FOR SELECT
TO anon, authenticated
USING (true);


REVOKE INSERT, UPDATE, DELETE ON public.nav_state FROM anon, authenticated;


SELECT grantee, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND table_name = 'nav_state'
ORDER BY grantee, privilege_type;


select table_schema, table_name, table_type
from information_schema.tables
where table_schema = 'public' and table_name = 'nav_state';


select schemaname, tablename
from pg_tables
where schemaname='public' and tablename='nav_state';

select schemaname, viewname
from pg_views
where schemaname='public' and viewname='nav_state';


select grantee, privilege_type
from information_schema.role_table_grants
where table_schema='public' and table_name='nav_state'
order by grantee, privilege_type;


select schemaname, tablename
from pg_tables
where schemaname='public' and tablename='nav_state';

grant usage on schema public to anon, authenticated;
grant select on public.nav_state to anon, authenticated;


select grantee, privilege_type
from information_schema.role_table_grants
where table_schema='public' and table_name='nav_state'
order by grantee, privilege_type;


select schemaname, tablename
from pg_tables
where schemaname='public' and tablename='nav_state';



select
  ordinal_position,
  column_name,
  data_type,
  is_nullable
from information_schema.columns
where table_schema = 'public'
  and table_name   = 'nav_state'
order by ordinal_position;



alter function public.sync_nav_state_from_rover_commands()
security definer;

alter function public.sync_nav_state_from_rover_commands()
set search_path = public;

alter function public.sync_nav_state_from_rover_commands()
owner to postgres;


select
  n.nspname as schema,
  p.proname as function_name,
  pg_get_function_identity_arguments(p.oid) as args
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname='public'
  and p.proname='sync_nav_state_from_rover_commands';



select
  c.relname,
  c.relrowsecurity as rls_enabled,
  c.relforcerowsecurity as rls_forced
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname='public'
  and c.relname='nav_state';


select *
from pg_policies
where schemaname='public'
  and tablename='nav_state';



select
  ordinal_position,
  column_name,
  data_type,
  is_nullable
from information_schema.columns
where table_schema = 'public'
  and table_name   = 'nav_state'
order by ordinal_position;



-- Give API roles permission to read
grant select on table public.nav_state to anon, authenticated;

-- If you're using RLS (recommended), enable it and allow read
alter table public.nav_state enable row level security;

drop policy if exists "nav_state_select_all" on public.nav_state;
create policy "nav_state_select_all"
on public.nav_state
for select
to anon, authenticated
using (true);




-- Enable RLS
ALTER TABLE nav_state ENABLE ROW LEVEL SECURITY;

-- Allow all operations for now (you can restrict later)
CREATE POLICY "Public access to nav_state"
ON nav_state
FOR ALL
TO public
USING (true)
WITH CHECK (true);


ALTER TABLE rover_commands ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public access to rover_commands" ON rover_commands FOR ALL TO public USING (true) WITH CHECK (true);

ALTER TABLE telemetry_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public access to telemetry_snapshots" ON telemetry_snapshots FOR ALL TO public USING (true) WITH CHECK (true);

ALTER TABLE pose_samples ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public access to pose_samples" ON pose_samples FOR ALL TO public USING (true) WITH CHECK (true);




-- For nav_state table
ALTER TABLE nav_state ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Public access to nav_state" ON nav_state;
CREATE POLICY "Public access to nav_state"
ON nav_state
FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);

-- For rover_commands table
ALTER TABLE rover_commands ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Public access to rover_commands" ON rover_commands;
CREATE POLICY "Public access to rover_commands"
ON rover_commands
FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);

-- For telemetry_snapshots table
ALTER TABLE telemetry_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Public access to telemetry_snapshots" ON telemetry_snapshots;
CREATE POLICY "Public access to telemetry_snapshots"
ON telemetry_snapshots
FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);

-- For pose_samples table
ALTER TABLE pose_samples ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Public access to pose_samples" ON pose_samples;
CREATE POLICY "Public access to pose_samples"
ON pose_samples
FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);



-- Check if RLS is enabled
SELECT tablename, rowsecurity 
FROM pg_tables 
WHERE schemaname = 'public' 
AND tablename IN ('nav_state', 'rover_commands', 'telemetry_snapshots', 'pose_samples');

-- Check existing policies
SELECT schemaname, tablename, policyname, permissive, roles, cmd
FROM pg_policies
WHERE tablename IN ('nav_state', 'rover_commands', 'telemetry_snapshots', 'pose_samples');



-- First, check what policies exist
SELECT policyname, cmd FROM pg_policies WHERE tablename = 'nav_state';

-- Drop ALL existing policies on nav_state
DO $$ 
DECLARE
    r RECORD;
BEGIN
    FOR r IN (SELECT policyname FROM pg_policies WHERE tablename = 'nav_state') 
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON nav_state', r.policyname);
    END LOOP;
END $$;

-- Make sure RLS is enabled
ALTER TABLE nav_state ENABLE ROW LEVEL SECURITY;

-- Create a single permissive policy for ALL operations
CREATE POLICY "allow_all_on_nav_state"
ON nav_state
FOR ALL
TO public
USING (true)
WITH CHECK (true);

-- Verify it was created
SELECT policyname, cmd, roles FROM pg_policies WHERE tablename = 'nav_state';



-- ==========================================
-- CLEAN UP nav_state policies
-- ==========================================
DROP POLICY IF EXISTS "read nav_state for authenticated" ON nav_state;
DROP POLICY IF EXISTS "Enable all for service role" ON nav_state;
DROP POLICY IF EXISTS "Allow anon read nav_state" ON nav_state;
DROP POLICY IF EXISTS "read nav_state" ON nav_state;
DROP POLICY IF EXISTS "nav_state_write_postgres" ON nav_state;
DROP POLICY IF EXISTS "nav_state_update_postgres" ON nav_state;
DROP POLICY IF EXISTS "nav_state_select_all" ON nav_state;
DROP POLICY IF EXISTS "Public access to nav_state" ON nav_state;

-- Create ONE simple policy for nav_state
CREATE POLICY "allow_all_nav_state"
ON nav_state
FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);

-- ==========================================
-- CLEAN UP rover_commands policies
-- ==========================================
DROP POLICY IF EXISTS "Enable all for service role" ON rover_commands;
DROP POLICY IF EXISTS "Allow anon insert/update rover_commands" ON rover_commands;
DROP POLICY IF EXISTS "rw rover_commands" ON rover_commands;
DROP POLICY IF EXISTS "Allow select rover_commands" ON rover_commands;
DROP POLICY IF EXISTS "Allow update rover_commands" ON rover_commands;
DROP POLICY IF EXISTS "Public access to rover_commands" ON rover_commands;

-- Create ONE simple policy for rover_commands
CREATE POLICY "allow_all_rover_commands"
ON rover_commands
FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);

-- ==========================================
-- CLEAN UP telemetry_snapshots policies
-- ==========================================
DROP POLICY IF EXISTS "Enable read access for all users" ON telemetry_snapshots;
DROP POLICY IF EXISTS "Enable insert for authenticated users only" ON telemetry_snapshots;
DROP POLICY IF EXISTS "Public access to telemetry_snapshots" ON telemetry_snapshots;

-- Create ONE simple policy for telemetry_snapshots
CREATE POLICY "allow_all_telemetry_snapshots"
ON telemetry_snapshots
FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);

-- ==========================================
-- CLEAN UP pose_samples policies
-- ==========================================
DROP POLICY IF EXISTS "Enable read access for all users" ON pose_samples;
DROP POLICY IF EXISTS "Enable insert for authenticated users only" ON pose_samples;
DROP POLICY IF EXISTS "Public access to pose_samples" ON pose_samples;

-- Create ONE simple policy for pose_samples
CREATE POLICY "allow_all_pose_samples"
ON pose_samples
FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);

-- ==========================================
-- VERIFY the policies
-- ==========================================
SELECT tablename, policyname, cmd, roles
FROM pg_policies
WHERE tablename IN ('nav_state', 'rover_commands', 'telemetry_snapshots', 'pose_samples')
ORDER BY tablename;




-- Check if the row exists
SELECT * FROM rover_commands WHERE robot_id = 'rover_01';


-- If it doesn't exist, insert it
INSERT INTO rover_commands (robot_id, mode, manual_left_speed, manual_right_speed, target_distance, target_heading, emergency_stop, reset_luggage, command_version)
VALUES ('rover_01', 'auto', 0.0, 0.0, 35.0, 0.0, false, false, 0)
ON CONFLICT (robot_id) DO NOTHING;



-- TEMPORARILY disable RLS on ALL tables
ALTER TABLE nav_state DISABLE ROW LEVEL SECURITY;
ALTER TABLE rover_commands DISABLE ROW LEVEL SECURITY;
ALTER TABLE telemetry_snapshots DISABLE ROW LEVEL SECURITY;
ALTER TABLE pose_samples DISABLE ROW LEVEL SECURITY;

-- Verify RLS is disabled
SELECT tablename, rowsecurity 
FROM pg_tables 
WHERE schemaname = 'public' 
AND tablename IN ('nav_state', 'rover_commands', 'telemetry_snapshots', 'pose_samples');



UPDATE rover_commands 
SET mode = 'manual', 
    emergency_stop = false,
    command_version = command_version + 1
WHERE robot_id = 'rover_01';

-- Check if it worked
SELECT * FROM rover_commands WHERE robot_id = 'rover_01';


SELECT trigger_name, event_manipulation, event_object_table, action_statement
FROM information_schema.triggers
WHERE event_object_schema = 'public'
AND event_object_table IN ('nav_state', 'rover_commands');



-- Make absolutely sure nav_state allows everything
ALTER TABLE nav_state ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "allow_all_nav_state" ON nav_state;

CREATE POLICY "allow_all_nav_state"
ON nav_state
FOR ALL
TO anon, authenticated, public
USING (true)
WITH CHECK (true);

-- Verify
SELECT tablename, policyname, cmd, roles 
FROM pg_policies 
WHERE tablename = 'nav_state';


ALTER TABLE nav_state DISABLE ROW LEVEL SECURITY;




-- Check for triggers
SELECT 
    trigger_name,
    event_object_table as table_name,
    action_statement,
    action_timing,
    event_manipulation
FROM information_schema.triggers
WHERE event_object_schema = 'public'
ORDER BY event_object_table;

-- Check for functions that might reference nav_state
SELECT 
    routine_name,
    routine_definition
FROM information_schema.routines
WHERE routine_schema = 'public'
AND routine_definition ILIKE '%nav_state%';


-- Check who owns the tables
SELECT 
    tablename,
    tableowner
FROM pg_tables
WHERE schemaname = 'public'
AND tablename IN ('nav_state', 'rover_commands');

-- Check current role
SELECT current_user, session_user;






-- Try to update rover_commands directly
UPDATE rover_commands
SET mode = 'manual', emergency_stop = false
WHERE robot_id = 'rover_01';

-- Check if it worked
SELECT * FROM rover_commands WHERE robot_id = 'rover_01';



alter table public.rover_commands enable row level security;


drop policy if exists "anon select rover_commands" on public.rover_commands;

create policy "anon select rover_commands"
on public.rover_commands
for select
to anon
using (true);



drop policy if exists "anon update rover_commands" on public.rover_commands;

create policy "anon update rover_commands"
on public.rover_commands
for update
to anon
using (true)
with check (true);



drop policy if exists "anon insert rover_commands" on public.rover_commands;

create policy "anon insert rover_commands"
on public.rover_commands
for insert
to anon
with check (true);





-- 0) Make sure we're in the right schema/table
-- (optional check)
select to_regclass('public.rover_commands') as rover_commands_table;

-- 1) Allow API roles to use the schema
grant usage on schema public to anon, authenticated;

-- 2) Give anon/authenticated privileges on the table
grant select, insert, update, delete on table public.rover_commands to anon, authenticated;

-- 3) If rover_commands has a BIGSERIAL id, you MUST grant sequence usage too
-- Find the sequence name and grant it:
do $$
declare seq_name text;
begin
  select pg_get_serial_sequence('public.rover_commands', 'id') into seq_name;
  if seq_name is not null then
    execute format('grant usage, select on sequence %s to anon, authenticated;', seq_name);
  end if;
end $$;

-- 4) Enable RLS (recommended) + add policies for anon
alter table public.rover_commands enable row level security;

drop policy if exists "anon select rover_commands" on public.rover_commands;
create policy "anon select rover_commands"
on public.rover_commands
for select
to anon
using (true);

drop policy if exists "anon insert rover_commands" on public.rover_commands;
create policy "anon insert rover_commands"
on public.rover_commands
for insert
to anon
with check (true);

drop policy if exists "anon update rover_commands" on public.rover_commands;
create policy "anon update rover_commands"
on public.rover_commands
for update
to anon
using (true)
with check (true);







-- 1) Schema usage
grant usage on schema public to anon, authenticated;

-- 2) Table privileges
grant select, insert, update, delete on table public.rover_commands to anon, authenticated;

-- 3) Enable RLS + policies (for demo: allow anon read/write)
alter table public.rover_commands enable row level security;

drop policy if exists "anon select rover_commands" on public.rover_commands;
create policy "anon select rover_commands"
on public.rover_commands
for select
to anon
using (true);

drop policy if exists "anon insert rover_commands" on public.rover_commands;
create policy "anon insert rover_commands"
on public.rover_commands
for insert
to anon
with check (true);

drop policy if exists "anon update rover_commands" on public.rover_commands;
create policy "anon update rover_commands"
on public.rover_commands
for update
to anon
using (true)
with check (true);




select table_schema, table_name
from information_schema.tables
where table_name = 'rover_commands';










do $$
declare r record;
begin
  for r in
    select sequence_schema, sequence_name
    from information_schema.sequences
    where sequence_schema = 'public'
      and sequence_name ilike '%rover_commands%'
  loop
    execute format(
      'grant usage, select on sequence %I.%I to anon, authenticated;',
      r.sequence_schema, r.sequence_name
    );
  end loop;
end $$;









select
  schemaname,
  tablename,
  policyname,
  permissive,
  roles,
  cmd,
  qual,
  with_check
from pg_policies
where schemaname = 'public'
  and tablename = 'rover_commands'
order by policyname;



select grantee, privilege_type
from information_schema.role_table_grants
where table_schema='public'
  and table_name='rover_commands'
order by grantee, privilege_type;





update public.rover_commands
set manual_left_speed = 12,
    manual_right_speed = 34,
    command_version = command_version + 1,
    command_updated_at = now()
where robot_id = 'rover_01';




-- apply to these tables your app touches
do $$
declare t text;
begin
  foreach t in array array['pose_samples','telemetry_snapshots','nav_state'] loop
    execute format('grant usage on schema public to anon, authenticated;');
    execute format('grant select, insert, update, delete on table public.%I to anon, authenticated;', t);
    execute format('alter table public.%I enable row level security;', t);

    execute format('drop policy if exists "anon select %I" on public.%I;', t, t);
    execute format('create policy "anon select %I" on public.%I for select to anon using (true);', t, t);

    execute format('drop policy if exists "anon insert %I" on public.%I;', t, t);
    execute format('create policy "anon insert %I" on public.%I for insert to anon with check (true);', t, t);

    execute format('drop policy if exists "anon update %I" on public.%I;', t, t);
    execute format('create policy "anon update %I" on public.%I for update to anon using (true) with check (true);', t, t);

    execute format('drop policy if exists "anon delete %I" on public.%I;', t, t);
    execute format('create policy "anon delete %I" on public.%I for delete to anon using (true);', t, t);
  end loop;
end $$;



create table if not exists public.tracking_samples (
  id bigserial primary key,
  ts timestamptz not null default now(),
  tag_id text not null default 'tag_01',
  floor_id text not null default 'L1',
  x_m double precision not null,
  y_m double precision not null,
  angle_deg double precision not null,
  distance_m double precision not null,
  x_raw double precision,
  y_raw double precision
);

create index if not exists tracking_samples_ts_idx
  on public.tracking_samples (ts desc);

create index if not exists tracking_samples_tag_ts_idx
  on public.tracking_samples (tag_id, ts desc);


alter table public.tracking_samples enable row level security;


create policy "Allow public read tracking"
on public.tracking_samples
for select
to anon
using (true);


create policy "Allow public read pose"
on public.pose_samples
for select
to anon
using (true);



alter table public.nav_state
add column if not exists floor_id text default 'L1';


alter table public.nav_state enable row level security;

create policy "nav_state read anon"
on public.nav_state
for select
to anon
using (true);


insert into public.nav_state (robot_id, floor_id)
values ('rover_01', 'L1')
on conflict (robot_id) do nothing;



SELECT * FROM rover_commands;


update public.rover_commands
set manual_left_speed = 12,
    manual_right_speed = 34,
    command_version = command_version + 1,
    command_updated_at = now()
where robot_id = 'rover_01';



alter table public.rover_commands
  add column if not exists obstacle_avoid_enabled boolean not null default true,
  add column if not exists obstacle_threshold_cm integer not null default 61,
  add column if not exists obstacle_clear_margin_cm integer not null default 8,
  add column if not exists obstacle_action text not null default 'avoid',
  add column if not exists clear_obstacle_override boolean not null default false;

-- Optional: constrain obstacle_action values
do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'rover_commands_obstacle_action_check'
  ) then
    alter table public.rover_commands
      add constraint rover_commands_obstacle_action_check
      check (obstacle_action in ('stop', 'avoid'));
  end if;
end $$;




-- Telemetry: add robot_id + obstacle + ultrasonic details
alter table telemetry_snapshots
  add column if not exists robot_id text,
  add column if not exists obstacle_reason text,
  add column if not exists ultra_front_cm integer,
  add column if not exists ultra_right_cm integer,
  add column if not exists ultra_left_cm integer,
  add column if not exists ultra_back_cm integer,
  add column if not exists created_at timestamptz default now();

create index if not exists telemetry_robot_time_idx
  on telemetry_snapshots (robot_id, created_at desc);

-- Commands: add obstacle control fields your Python expects
alter table rover_commands
  add column if not exists obstacle_avoid_enabled boolean default true,
  add column if not exists obstacle_threshold_cm integer default 61,
  add column if not exists obstacle_clear_margin_cm integer default 8,
  add column if not exists obstacle_action text default 'avoid',
  add column if not exists clear_obstacle_override boolean default false;






-- 1. See ALL columns that actually exist
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'rover_commands'
ORDER BY ordinal_position;

-- 2. See the actual current row
SELECT * FROM rover_commands WHERE robot_id = 'rover_01';

-- 3. Check if RLS is blocking writes
SELECT schemaname, tablename, rowsecurity 
FROM pg_tables 
WHERE tablename IN ('rover_commands', 'telemetry_snapshots', 'nav_state');

-- 4. Check RLS policies
SELECT tablename, policyname, cmd, qual, with_check
FROM pg_policies
WHERE tablename IN ('rover_commands', 'telemetry_snapshots', 'nav_state');



-- Check exact column names on rover_commands
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'rover_commands'
ORDER BY ordinal_position;

-- See actual current row values
SELECT * FROM rover_commands WHERE robot_id = 'rover_01';




CREATE OR REPLACE FUNCTION send_rover_command(
  p_robot_id text,
  p_updates jsonb
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  UPDATE rover_commands
  SET
    mode = COALESCE(p_updates->>'mode', mode),
    manual_left_speed = COALESCE((p_updates->>'manual_left_speed')::float, manual_left_speed),
    manual_right_speed = COALESCE((p_updates->>'manual_right_speed')::float, manual_right_speed),
    emergency_stop = COALESCE((p_updates->>'emergency_stop')::boolean, emergency_stop),
    target_distance = COALESCE((p_updates->>'target_distance')::float, target_distance),
    target_heading = COALESCE((p_updates->>'target_heading')::float, target_heading),
    reset_luggage = COALESCE((p_updates->>'reset_luggage')::boolean, reset_luggage),
    obstacle_avoid_enabled = COALESCE((p_updates->>'obstacle_avoid_enabled')::boolean, obstacle_avoid_enabled),
    obstacle_threshold_cm = COALESCE((p_updates->>'obstacle_threshold_cm')::int, obstacle_threshold_cm),
    obstacle_clear_margin_cm = COALESCE((p_updates->>'obstacle_clear_margin_cm')::int, obstacle_clear_margin_cm),
    obstacle_action = COALESCE(p_updates->>'obstacle_action', obstacle_action),
    clear_obstacle_override = COALESCE((p_updates->>'clear_obstacle_override')::boolean, clear_obstacle_override),
    command_version = command_version + 1,
    command_updated_at = now()
  WHERE robot_id = p_robot_id;
END;
$$;

-- Grant access to anon role
GRANT EXECUTE ON FUNCTION send_rover_command(text, jsonb) TO anon;
GRANT EXECUTE ON FUNCTION send_rover_command(text, jsonb) TO authenticated;


alter table telemetry_snapshots
add column if not exists obstacle_avoid_active boolean default false;





-- ============================================================
-- UWB Positions table
-- Run this in Supabase SQL Editor (Dashboard → SQL Editor → New query)
-- ============================================================

-- One live row per robot.
-- The UWB Pi upserts this row at ~10 Hz.
-- The rover Pi and Flutter app read from it.

CREATE TABLE IF NOT EXISTS uwb_positions (
    robot_id    TEXT        PRIMARY KEY,
    x_m         DOUBLE PRECISION NOT NULL DEFAULT 0.0,  -- tag X (m), 0 = anchor midpoint
    y_m         DOUBLE PRECISION NOT NULL DEFAULT 0.35, -- tag Y (m), 0 = wall, + = room
    angle_deg   DOUBLE PRECISION NOT NULL DEFAULT 0.0,  -- angle from Y axis (degrees)
    distance_m  DOUBLE PRECISION NOT NULL DEFAULT 0.35, -- straight-line dist tag↔origin
    d1_m        DOUBLE PRECISION NOT NULL DEFAULT 0.35, -- range to anchor 1 (m)
    d2_m        DOUBLE PRECISION NOT NULL DEFAULT 0.35, -- range to anchor 2 (m)
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed a default row so the rover Pi can always read something
INSERT INTO uwb_positions (robot_id, x_m, y_m, angle_deg, distance_m, d1_m, d2_m)
VALUES ('rover_01', 0.0, 0.35, 0.0, 0.35, 0.60, 0.60)
ON CONFLICT (robot_id) DO NOTHING;

-- Enable Realtime so Flutter can stream changes (optional but nice)
ALTER TABLE uwb_positions REPLICA IDENTITY FULL;

-- RLS: allow anon read (UWB Pi uses anon key to write via REST)
ALTER TABLE uwb_positions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon can read uwb_positions"
    ON uwb_positions FOR SELECT USING (true);

CREATE POLICY "anon can upsert uwb_positions"
    ON uwb_positions FOR ALL USING (true) WITH CHECK (true);

-- ============================================================
-- Add uwb_live column to telemetry_snapshots (if not present)
-- ============================================================
ALTER TABLE telemetry_snapshots
    ADD COLUMN IF NOT EXISTS uwb_live BOOLEAN DEFAULT FALSE;




create policy "allow read uwb positions"
on public.uwb_positions
for select
to anon
using (true);






SELECT * FROM uwb_positions;




SELECT * FROM rover_commands;


update rover_commands
set mode = 'auto',
    command_version = command_version + 1,
    command_updated_at = now()
where robot_id = 'rover_01';





create table if not exists pairing_sessions (
  robot_id          text primary key default 'rover_01',
  session_token     text,
  paired            boolean default false,
  created_at        timestamptz default now(),
  expires_at        timestamptz,
  confirmed_at      timestamptz,
  user_code         text
);




-- Enable RLS on the table
alter table pairing_sessions enable row level security;

-- Allow anyone with the anon key to read their own robot's row
-- (app needs to read paired=true, rover needs to read session_token)
create policy "anon can read pairing_sessions"
  on pairing_sessions
  for select
  using (true);

-- Allow anyone with the anon key to insert/upsert
-- (app writes the initial pairing request)
create policy "anon can insert pairing_sessions"
  on pairing_sessions
  for insert
  with check (true);

-- Allow anyone with the anon key to update
-- (rover writes paired=true, app writes user_code)
create policy "anon can update pairing_sessions"
  on pairing_sessions
  for update
  using (true)
  with check (true);






-- =========================================
-- 1) Rover heartbeat (written by rover Pi)
-- =========================================
create table if not exists public.robot_heartbeat (
  robot_id            text primary key,
  updated_at          timestamptz not null default now(),

  loop_ms             integer,
  control_mode        text,

  uwb_live            boolean,
  uwb_age_s           double precision,

  ultrasonic_enabled  boolean,
  obstacle_override   boolean
);

-- Keep updated_at fresh on upsert/update (optional but recommended)
create or replace function public.set_updated_at_robot_heartbeat()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

drop trigger if exists trg_robot_heartbeat_updated_at on public.robot_heartbeat;
create trigger trg_robot_heartbeat_updated_at
before update on public.robot_heartbeat
for each row execute function public.set_updated_at_robot_heartbeat();


-- =========================================
-- 2) UWB heartbeat (written by UWB Pi)
-- =========================================
create table if not exists public.uwb_heartbeat (
  robot_id       text primary key,
  updated_at     timestamptz not null default now(),

  a1_connected   boolean,
  a2_connected   boolean,

  a1_age_s       double precision,
  a2_age_s       double precision
);

create or replace function public.set_updated_at_uwb_heartbeat()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

drop trigger if exists trg_uwb_heartbeat_updated_at on public.uwb_heartbeat;
create trigger trg_uwb_heartbeat_updated_at
before update on public.uwb_heartbeat
for each row execute function public.set_updated_at_uwb_heartbeat();


-- =========================================
-- Helpful indexes (optional)
-- (PK already indexed; these are for sorting by time if you ever store history later)
-- =========================================
create index if not exists idx_robot_heartbeat_updated_at
  on public.robot_heartbeat (updated_at desc);

create index if not exists idx_uwb_heartbeat_updated_at
  on public.uwb_heartbeat (updated_at desc);




-- =========================================================
-- Heartbeat tables RLS policies
-- Goal:
--   - Allow public read (anon + authenticated)
--   - Disallow all client writes (insert/update/delete)
--   - Server writes via service_role key (bypasses RLS)
-- =========================================================

-- 1) Ensure tables exist (skip if already created)
create table if not exists public.robot_heartbeat (
  robot_id            text primary key,
  updated_at          timestamptz not null default now(),
  loop_ms             integer,
  control_mode        text,
  uwb_live            boolean,
  uwb_age_s           double precision,
  ultrasonic_enabled  boolean,
  obstacle_override   boolean
);

create table if not exists public.uwb_heartbeat (
  robot_id       text primary key,
  updated_at     timestamptz not null default now(),
  a1_connected   boolean,
  a2_connected   boolean,
  a1_age_s       double precision,
  a2_age_s       double precision
);

-- 2) Enable RLS
alter table public.robot_heartbeat enable row level security;
alter table public.uwb_heartbeat  enable row level security;

-- 3) Remove any old policies (safe to re-run)
drop policy if exists "robot_heartbeat_read_all" on public.robot_heartbeat;
drop policy if exists "uwb_heartbeat_read_all" on public.uwb_heartbeat;

drop policy if exists "robot_heartbeat_insert" on public.robot_heartbeat;
drop policy if exists "robot_heartbeat_update" on public.robot_heartbeat;
drop policy if exists "robot_heartbeat_delete" on public.robot_heartbeat;

drop policy if exists "uwb_heartbeat_insert" on public.uwb_heartbeat;
drop policy if exists "uwb_heartbeat_update" on public.uwb_heartbeat;
drop policy if exists "uwb_heartbeat_delete" on public.uwb_heartbeat;

-- 4) Allow READ to everyone (anon + authenticated)
create policy "robot_heartbeat_read_all"
on public.robot_heartbeat
for select
to anon, authenticated
using (true);

create policy "uwb_heartbeat_read_all"
on public.uwb_heartbeat
for select
to anon, authenticated
using (true);

-- 5) Explicitly BLOCK writes for client roles (optional but clear)
-- Without policies, writes are denied anyway under RLS.
-- These "false" policies make intent explicit.

create policy "robot_heartbeat_insert"
on public.robot_heartbeat
for insert
to anon, authenticated
with check (false);

create policy "robot_heartbeat_update"
on public.robot_heartbeat
for update
to anon, authenticated
using (false)
with check (false);

create policy "robot_heartbeat_delete"
on public.robot_heartbeat
for delete
to anon, authenticated
using (false);

create policy "uwb_heartbeat_insert"
on public.uwb_heartbeat
for insert
to anon, authenticated
with check (false);

create policy "uwb_heartbeat_update"
on public.uwb_heartbeat
for update
to anon, authenticated
using (false)
with check (false);

create policy "uwb_heartbeat_delete"
on public.uwb_heartbeat
for delete
to anon, authenticated
using (false);






-- UWB health table (only if needed)
CREATE TABLE IF NOT EXISTS uwb_health (
  robot_id TEXT PRIMARY KEY,
  anchor1_connected BOOLEAN,
  anchor2_connected BOOLEAN,
  anchor1_range_m FLOAT,
  anchor2_range_m FLOAT,
  anchor1_age_s FLOAT,
  anchor2_age_s FLOAT,
  publish_rate_hz FLOAT,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Add new columns to existing tables
ALTER TABLE rover_commands 
  ADD COLUMN IF NOT EXISTS manual_override_mode BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS smart_recovery BOOLEAN DEFAULT FALSE;

ALTER TABLE telemetry_snapshots 
  ADD COLUMN IF NOT EXISTS uwb_confidence FLOAT,
  ADD COLUMN IF NOT EXISTS loop_time_ms FLOAT,
  ADD COLUMN IF NOT EXISTS state TEXT;

ALTER TABLE uwb_positions 
  ADD COLUMN IF NOT EXISTS confidence FLOAT;




-- Ensure robot_heartbeat has all required columns
DO $$ 
BEGIN
  -- Add columns if they don't exist
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                 WHERE table_name='robot_heartbeat' AND column_name='cpu_temp') THEN
    ALTER TABLE robot_heartbeat ADD COLUMN cpu_temp FLOAT;
  END IF;
  
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                 WHERE table_name='robot_heartbeat' AND column_name='uptime_s') THEN
    ALTER TABLE robot_heartbeat ADD COLUMN uptime_s FLOAT;
  END IF;
  
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                 WHERE table_name='robot_heartbeat' AND column_name='loop_time_ms') THEN
    ALTER TABLE robot_heartbeat ADD COLUMN loop_time_ms FLOAT;
  END IF;
  
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                 WHERE table_name='robot_heartbeat' AND column_name='uwb_live') THEN
    ALTER TABLE robot_heartbeat ADD COLUMN uwb_live BOOLEAN;
  END IF;
  
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                 WHERE table_name='robot_heartbeat' AND column_name='ultra_active') THEN
    ALTER TABLE robot_heartbeat ADD COLUMN ultra_active BOOLEAN;
  END IF;
  
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                 WHERE table_name='robot_heartbeat' AND column_name='updated_at') THEN
    ALTER TABLE robot_heartbeat ADD COLUMN updated_at TIMESTAMPTZ DEFAULT NOW();
  END IF;
END $$;





-- Only use if you want to start fresh and lose existing data
DROP TABLE IF EXISTS robot_heartbeat CASCADE;

CREATE TABLE robot_heartbeat (
  robot_id TEXT PRIMARY KEY,
  cpu_temp FLOAT,
  uptime_s FLOAT,
  loop_time_ms FLOAT,
  uwb_live BOOLEAN,
  ultra_active BOOLEAN,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);






-- Create tables only if they don't exist
CREATE TABLE IF NOT EXISTS robot_heartbeat (
  robot_id TEXT PRIMARY KEY,
  cpu_temp FLOAT,
  uptime_s FLOAT,
  loop_time_ms FLOAT,
  uwb_live BOOLEAN,
  ultra_active BOOLEAN,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS uwb_health (
  robot_id TEXT PRIMARY KEY,
  anchor1_connected BOOLEAN,
  anchor2_connected BOOLEAN,
  anchor1_range_m FLOAT,
  anchor2_range_m FLOAT,
  anchor1_age_s FLOAT,
  anchor2_age_s FLOAT,
  publish_rate_hz FLOAT,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Safely add columns to existing tables
ALTER TABLE rover_commands 
  ADD COLUMN IF NOT EXISTS manual_override_mode BOOLEAN DEFAULT FALSE;
  
ALTER TABLE rover_commands 
  ADD COLUMN IF NOT EXISTS smart_recovery BOOLEAN DEFAULT FALSE;

ALTER TABLE telemetry_snapshots 
  ADD COLUMN IF NOT EXISTS uwb_confidence FLOAT;
  
ALTER TABLE telemetry_snapshots 
  ADD COLUMN IF NOT EXISTS loop_time_ms FLOAT;
  
ALTER TABLE telemetry_snapshots 
  ADD COLUMN IF NOT EXISTS state TEXT;

ALTER TABLE uwb_positions 
  ADD COLUMN IF NOT EXISTS confidence FLOAT;










-- ============================================================================
-- RLS Policies for Rover System
-- ============================================================================

-- 1. pairing_sessions table
-- ============================================================================
ALTER TABLE pairing_sessions ENABLE ROW LEVEL SECURITY;

-- Allow public read access (app checks pairing status)
CREATE POLICY "Allow public read on pairing_sessions"
ON pairing_sessions FOR SELECT
USING (true);

-- Allow public insert (app initiates pairing)
CREATE POLICY "Allow public insert on pairing_sessions"
ON pairing_sessions FOR INSERT
WITH CHECK (true);

-- Allow public update (rover confirms pairing)
CREATE POLICY "Allow public update on pairing_sessions"
ON pairing_sessions FOR UPDATE
USING (true);


-- 2. rover_commands table
-- ============================================================================
ALTER TABLE rover_commands ENABLE ROW LEVEL SECURITY;

-- Allow public read (rover reads commands)
CREATE POLICY "Allow public read on rover_commands"
ON rover_commands FOR SELECT
USING (true);

-- Allow public insert (app sends commands)
CREATE POLICY "Allow public insert on rover_commands"
ON rover_commands FOR INSERT
WITH CHECK (true);

-- Allow public update (app updates commands)
CREATE POLICY "Allow public update on rover_commands"
ON rover_commands FOR UPDATE
USING (true);


-- 3. uwb_positions table
-- ============================================================================
ALTER TABLE uwb_positions ENABLE ROW LEVEL SECURITY;

-- Allow public read (app and rover read UWB positions)
CREATE POLICY "Allow public read on uwb_positions"
ON uwb_positions FOR SELECT
USING (true);

-- Allow public insert/upsert (UWB Pi publishes positions)
CREATE POLICY "Allow public insert on uwb_positions"
ON uwb_positions FOR INSERT
WITH CHECK (true);

-- Allow public update (upsert operation)
CREATE POLICY "Allow public update on uwb_positions"
ON uwb_positions FOR UPDATE
USING (true);


-- 4. nav_state table
-- ============================================================================
ALTER TABLE nav_state ENABLE ROW LEVEL SECURITY;

-- Allow public read (app monitors motor commands)
CREATE POLICY "Allow public read on nav_state"
ON nav_state FOR SELECT
USING (true);

-- Allow public insert/upsert (rover publishes motor state)
CREATE POLICY "Allow public insert on nav_state"
ON nav_state FOR INSERT
WITH CHECK (true);

-- Allow public update (upsert operation)
CREATE POLICY "Allow public update on nav_state"
ON nav_state FOR UPDATE
USING (true);


-- 5. telemetry_snapshots table
-- ============================================================================
ALTER TABLE telemetry_snapshots ENABLE ROW LEVEL SECURITY;

-- Allow public read (app displays telemetry)
CREATE POLICY "Allow public read on telemetry_snapshots"
ON telemetry_snapshots FOR SELECT
USING (true);

-- Allow public insert (rover publishes telemetry)
CREATE POLICY "Allow public insert on telemetry_snapshots"
ON telemetry_snapshots FOR INSERT
WITH CHECK (true);


-- 6. rover_alerts table
-- ============================================================================
ALTER TABLE rover_alerts ENABLE ROW LEVEL SECURITY;

-- Allow public read (app displays alerts)
CREATE POLICY "Allow public read on rover_alerts"
ON rover_alerts FOR SELECT
USING (true);

-- Allow public insert (rover creates obstacle alerts)
CREATE POLICY "Allow public insert on rover_alerts"
ON rover_alerts FOR INSERT
WITH CHECK (true);


-- 7. robot_heartbeat table (NEW)
-- ============================================================================
ALTER TABLE robot_heartbeat ENABLE ROW LEVEL SECURITY;

-- Allow public read (app monitors system health)
CREATE POLICY "Allow public read on robot_heartbeat"
ON robot_heartbeat FOR SELECT
USING (true);

-- Allow public insert/upsert (rover publishes heartbeat)
CREATE POLICY "Allow public insert on robot_heartbeat"
ON robot_heartbeat FOR INSERT
WITH CHECK (true);

-- Allow public update (upsert operation)
CREATE POLICY "Allow public update on robot_heartbeat"
ON robot_heartbeat FOR UPDATE
USING (true);


-- 8. uwb_health table (NEW)
-- ============================================================================
ALTER TABLE uwb_health ENABLE ROW LEVEL SECURITY;

-- Allow public read (app monitors UWB anchor health)
CREATE POLICY "Allow public read on uwb_health"
ON uwb_health FOR SELECT
USING (true);

-- Allow public insert/upsert (UWB Pi publishes health)
CREATE POLICY "Allow public insert on uwb_health"
ON uwb_health FOR INSERT
WITH CHECK (true);

-- Allow public update (upsert operation)
CREATE POLICY "Allow public update on uwb_health"
ON uwb_health FOR UPDATE
USING (true);




CREATE TABLE rover_alerts (
  id BIGSERIAL PRIMARY KEY,
  robot_id TEXT NOT NULL,
  alert_type TEXT NOT NULL,
  message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE rover_alerts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow public read on rover_alerts" ON rover_alerts FOR SELECT USING (true);
CREATE POLICY "Allow public insert on rover_alerts" ON rover_alerts FOR INSERT WITH CHECK (true);


-- Drop existing policies and recreate
DROP POLICY IF EXISTS "Allow public read on rover_alerts" ON rover_alerts;
DROP POLICY IF EXISTS "Allow public insert on rover_alerts" ON rover_alerts;

ALTER TABLE rover_alerts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow public read on rover_alerts"
ON rover_alerts FOR SELECT
USING (true);

CREATE POLICY "Allow public insert on rover_alerts"
ON rover_alerts FOR INSERT
WITH CHECK (true);





-- ============================================================================
-- SAFE RLS POLICY SETUP - Won't fail on existing policies
-- ============================================================================

-- Drop and recreate approach (safest for updates)
-- This ensures clean policy state

-- rover_alerts policies
DROP POLICY IF EXISTS "Allow public read on rover_alerts" ON rover_alerts;
DROP POLICY IF EXISTS "Allow public insert on rover_alerts" ON rover_alerts;

CREATE POLICY "Allow public read on rover_alerts"
ON rover_alerts FOR SELECT
USING (true);

CREATE POLICY "Allow public insert on rover_alerts"
ON rover_alerts FOR INSERT
WITH CHECK (true);

-- Apply same pattern to other tables
-- pairing_sessions
DROP POLICY IF EXISTS "Allow public read on pairing_sessions" ON pairing_sessions;
DROP POLICY IF EXISTS "Allow public insert on pairing_sessions" ON pairing_sessions;
DROP POLICY IF EXISTS "Allow public update on pairing_sessions" ON pairing_sessions;

CREATE POLICY "Allow public read on pairing_sessions" ON pairing_sessions FOR SELECT USING (true);
CREATE POLICY "Allow public insert on pairing_sessions" ON pairing_sessions FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update on pairing_sessions" ON pairing_sessions FOR UPDATE USING (true);

-- rover_commands
DROP POLICY IF EXISTS "Allow public read on rover_commands" ON rover_commands;
DROP POLICY IF EXISTS "Allow public insert on rover_commands" ON rover_commands;
DROP POLICY IF EXISTS "Allow public update on rover_commands" ON rover_commands;

CREATE POLICY "Allow public read on rover_commands" ON rover_commands FOR SELECT USING (true);
CREATE POLICY "Allow public insert on rover_commands" ON rover_commands FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update on rover_commands" ON rover_commands FOR UPDATE USING (true);

-- uwb_positions
DROP POLICY IF EXISTS "Allow public read on uwb_positions" ON uwb_positions;
DROP POLICY IF EXISTS "Allow public insert on uwb_positions" ON uwb_positions;
DROP POLICY IF EXISTS "Allow public update on uwb_positions" ON uwb_positions;

CREATE POLICY "Allow public read on uwb_positions" ON uwb_positions FOR SELECT USING (true);
CREATE POLICY "Allow public insert on uwb_positions" ON uwb_positions FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update on uwb_positions" ON uwb_positions FOR UPDATE USING (true);

-- nav_state
DROP POLICY IF EXISTS "Allow public read on nav_state" ON nav_state;
DROP POLICY IF EXISTS "Allow public insert on nav_state" ON nav_state;
DROP POLICY IF EXISTS "Allow public update on nav_state" ON nav_state;

CREATE POLICY "Allow public read on nav_state" ON nav_state FOR SELECT USING (true);
CREATE POLICY "Allow public insert on nav_state" ON nav_state FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update on nav_state" ON nav_state FOR UPDATE USING (true);

-- telemetry_snapshots
DROP POLICY IF EXISTS "Allow public read on telemetry_snapshots" ON telemetry_snapshots;
DROP POLICY IF EXISTS "Allow public insert on telemetry_snapshots" ON telemetry_snapshots;

CREATE POLICY "Allow public read on telemetry_snapshots" ON telemetry_snapshots FOR SELECT USING (true);
CREATE POLICY "Allow public insert on telemetry_snapshots" ON telemetry_snapshots FOR INSERT WITH CHECK (true);

-- robot_heartbeat
DROP POLICY IF EXISTS "Allow public read on robot_heartbeat" ON robot_heartbeat;
DROP POLICY IF EXISTS "Allow public insert on robot_heartbeat" ON robot_heartbeat;
DROP POLICY IF EXISTS "Allow public update on robot_heartbeat" ON robot_heartbeat;

CREATE POLICY "Allow public read on robot_heartbeat" ON robot_heartbeat FOR SELECT USING (true);
CREATE POLICY "Allow public insert on robot_heartbeat" ON robot_heartbeat FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update on robot_heartbeat" ON robot_heartbeat FOR UPDATE USING (true);

-- uwb_health
DROP POLICY IF EXISTS "Allow public read on uwb_health" ON uwb_health;
DROP POLICY IF EXISTS "Allow public insert on uwb_health" ON uwb_health;
DROP POLICY IF EXISTS "Allow public update on uwb_health" ON uwb_health;

CREATE POLICY "Allow public read on uwb_health" ON uwb_health FOR SELECT USING (true);
CREATE POLICY "Allow public insert on uwb_health" ON uwb_health FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update on uwb_health" ON uwb_health FOR UPDATE USING (true);




ALTER TABLE rover_commands
  ADD COLUMN IF NOT EXISTS uwb_override_enabled BOOLEAN DEFAULT FALSE;



UPDATE rover_commands
SET uwb_override_enabled = TRUE,
    mode = 'manual',
    manual_left_speed = 20,
    manual_right_speed = 20,
    command_version = command_version + 1,
    command_updated_at = now()
WHERE robot_id = 'rover_01';




SELECT * FROM rover_commands;















CREATE OR REPLACE FUNCTION send_rover_command(
  p_robot_id TEXT,
  p_updates  JSONB
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  UPDATE rover_commands
  SET
    mode                   = COALESCE((p_updates->>'mode')::TEXT,                   mode),
    manual_left_speed      = COALESCE((p_updates->>'manual_left_speed')::FLOAT,     manual_left_speed),
    manual_right_speed     = COALESCE((p_updates->>'manual_right_speed')::FLOAT,    manual_right_speed),
    manual_override_mode   = COALESCE((p_updates->>'manual_override_mode')::BOOLEAN, manual_override_mode),
    emergency_stop         = COALESCE((p_updates->>'emergency_stop')::BOOLEAN,      emergency_stop),
    obstacle_avoid_enabled = COALESCE((p_updates->>'obstacle_avoid_enabled')::BOOLEAN, obstacle_avoid_enabled),
    obstacle_threshold_cm  = COALESCE((p_updates->>'obstacle_threshold_cm')::INT,   obstacle_threshold_cm),
    obstacle_action        = COALESCE((p_updates->>'obstacle_action')::TEXT,        obstacle_action),
    clear_obstacle_override= COALESCE((p_updates->>'clear_obstacle_override')::BOOLEAN, clear_obstacle_override),
    smart_recovery         = COALESCE((p_updates->>'smart_recovery')::BOOLEAN,      smart_recovery),
    reset_luggage          = COALESCE((p_updates->>'reset_luggage')::BOOLEAN,       reset_luggage),
    uwb_override_enabled   = COALESCE((p_updates->>'uwb_override_enabled')::BOOLEAN, uwb_override_enabled),
    session_token          = COALESCE((p_updates->>'session_token')::TEXT,          session_token),
    command_version        = command_version + 1,
    command_updated_at     = NOW()
  WHERE robot_id = p_robot_id;
END;
$$;

-- Grant execute to anon (Flutter uses anon key)
GRANT EXECUTE ON FUNCTION send_rover_command(TEXT, JSONB) TO anon;
GRANT EXECUTE ON FUNCTION send_rover_command(TEXT, JSONB) TO authenticated;









ALTER TABLE rover_commands
  ADD COLUMN IF NOT EXISTS session_token TEXT;




ALTER TABLE rover_commands
  ADD COLUMN IF NOT EXISTS session_token TEXT,
  ADD COLUMN IF NOT EXISTS manual_override_mode BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS smart_recovery BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS uwb_override_enabled BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS clear_obstacle_override BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS reset_luggage BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS command_updated_at TIMESTAMPTZ DEFAULT NOW();









create table if not exists uwb_positions (
  robot_id    text primary key,
  x_m         double precision,
  y_m         double precision,
  angle_deg   double precision,
  distance_m  double precision,
  d1_m        double precision,
  d2_m        double precision,
  confidence  double precision,
  updated_at  timestamptz
);



create table if not exists uwb_health (
  robot_id            text primary key,
  anchor1_connected   boolean,
  anchor2_connected   boolean,
  anchor1_range_m     double precision,
  anchor2_range_m     double precision,
  anchor1_age_s       double precision,
  anchor2_age_s       double precision,
  publish_rate_hz     double precision,
  updated_at          timestamptz
);





-- =========================================================
-- TABLE: uwb_health
-- =========================================================
create table if not exists public.uwb_health (
  robot_id            text primary key,
  anchor1_connected   boolean not null default false,
  anchor2_connected   boolean not null default false,
  anchor1_range_m     double precision,
  anchor2_range_m     double precision,
  anchor1_age_s       double precision,
  anchor2_age_s       double precision,
  publish_rate_hz     double precision,
  updated_at          timestamptz not null default now()
);

-- Helpful index for dashboards (optional)
create index if not exists uwb_health_updated_at_idx
  on public.uwb_health (updated_at desc);

-- =========================================================
-- RLS
-- =========================================================
alter table public.uwb_health enable row level security;

-- Allow anon to READ the rover row (demo mode)
drop policy if exists "uwb_health_read_rover_01" on public.uwb_health;
create policy "uwb_health_read_rover_01"
on public.uwb_health
for select
to anon
using (robot_id = 'rover_01');

-- Allow anon to UPSERT/UPDATE the rover row (demo mode)
drop policy if exists "uwb_health_write_rover_01" on public.uwb_health;
create policy "uwb_health_write_rover_01"
on public.uwb_health
for all
to anon
using (robot_id = 'rover_01')
with check (robot_id = 'rover_01');





-- =========================================================
-- TABLE: robot_heartbeat
-- =========================================================
create table if not exists public.robot_heartbeat (
  robot_id        text primary key,
  status          text not null default 'online',  -- online/offline/error/etc
  mode            text,                           -- auto/manual/waiting_for_pair/etc
  battery_v       double precision,
  wifi_rssi_dbm   integer,
  cpu_temp_c      double precision,
  last_seen_at    timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists robot_heartbeat_last_seen_idx
  on public.robot_heartbeat (last_seen_at desc);

-- Optional: auto-update updated_at on any update
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_robot_heartbeat_updated_at on public.robot_heartbeat;
create trigger trg_robot_heartbeat_updated_at
before update on public.robot_heartbeat
for each row execute function public.set_updated_at();

-- =========================================================
-- RLS
-- =========================================================
alter table public.robot_heartbeat enable row level security;

drop policy if exists "robot_heartbeat_read_rover_01" on public.robot_heartbeat;
create policy "robot_heartbeat_read_rover_01"
on public.robot_heartbeat
for select
to anon
using (robot_id = 'rover_01');

drop policy if exists "robot_heartbeat_write_rover_01" on public.robot_heartbeat;
create policy "robot_heartbeat_write_rover_01"
on public.robot_heartbeat
for all
to anon
using (robot_id = 'rover_01')
with check (robot_id = 'rover_01');






create table if not exists public.follow_distance_changes (
  id bigint generated by default as identity primary key,
  robot_id text not null,
  session_token text,
  previous_target_distance_cm double precision,
  new_target_distance_cm double precision not null,
  changed_from text not null default 'app',
  created_at timestamptz not null default now()
);

alter table public.follow_distance_changes enable row level security;





create policy "authenticated can insert follow distance history"
on public.follow_distance_changes
for insert
to authenticated
with check (true);


create policy "authenticated can read follow distance history"
on public.follow_distance_changes
for select
to authenticated
using (true);



alter table public.follow_distance_changes enable row level security;

drop policy if exists "authenticated can insert follow distance history"
on public.follow_distance_changes;



create policy "authenticated can insert follow distance history"
on public.follow_distance_changes
for insert
to authenticated
with check (true);


drop policy if exists "authenticated can read follow distance history"
on public.follow_distance_changes;


create policy "authenticated can read follow distance history"
on public.follow_distance_changes
for select
to authenticated
using (true);










alter table public.follow_distance_changes enable row level security;

drop policy if exists "authenticated can insert follow distance history"
on public.follow_distance_changes;

drop policy if exists "authenticated can read follow distance history"
on public.follow_distance_changes;

create policy "public can insert follow distance history"
on public.follow_distance_changes
for insert
to public
with check (robot_id = 'rover_01');

create policy "public can read follow distance history"
on public.follow_distance_changes
for select
to public
using (robot_id = 'rover_01');









create or replace function public.send_rover_command(
  p_robot_id text,
  p_updates jsonb
)
returns void
language plpgsql
security definer
as $$
declare
  v_existing jsonb;
  v_merged jsonb;
begin
  select to_jsonb(rc.*)
  into v_existing
  from public.rover_commands rc
  where rc.robot_id = p_robot_id
  limit 1;

  if v_existing is null then
    insert into public.rover_commands (
      robot_id,
      mode,
      target_distance,
      target_heading,
      manual_left_speed,
      manual_right_speed,
      emergency_stop,
      session_token,
      command_version,
      command_updated_at
    )
    values (
      p_robot_id,
      coalesce(p_updates->>'mode', 'auto'),
      coalesce((p_updates->>'target_distance')::double precision, 35.0),
      coalesce((p_updates->>'target_heading')::double precision, 0.0),
      coalesce((p_updates->>'manual_left_speed')::double precision, 0.0),
      coalesce((p_updates->>'manual_right_speed')::double precision, 0.0),
      coalesce((p_updates->>'emergency_stop')::boolean, false),
      p_updates->>'session_token',
      1,
      now()
    );
  else
    update public.rover_commands
    set
      mode = coalesce(p_updates->>'mode', mode),
      target_distance = coalesce((p_updates->>'target_distance')::double precision, target_distance),
      target_heading = coalesce((p_updates->>'target_heading')::double precision, target_heading),
      manual_left_speed = coalesce((p_updates->>'manual_left_speed')::double precision, manual_left_speed),
      manual_right_speed = coalesce((p_updates->>'manual_right_speed')::double precision, manual_right_speed),
      emergency_stop = coalesce((p_updates->>'emergency_stop')::boolean, emergency_stop),
      session_token = coalesce(p_updates->>'session_token', session_token),
      command_version = coalesce(command_version, 0) + 1,
      command_updated_at = now()
    where robot_id = p_robot_id;
  end if;
end;
$$;



select robot_id, target_distance, session_token, command_version, command_updated_at
from public.rover_commands
where robot_id = 'rover_01';





create table if not exists rover_live_state (
  robot_id text primary key,
  mode text,
  weight_kg double precision,
  obstacle_hold boolean default false,
  obstacle_avoid_active boolean default false,
  arrived boolean default false,
  luggage_fallen boolean default false,
  obstacle_reason text,
  updated_at timestamptz default now()
);




-- 1) Create the live-state table
create table if not exists public.rover_live_state (
  robot_id text primary key,
  mode text,
  weight_kg double precision,
  obstacle_hold boolean not null default false,
  obstacle_avoid_active boolean not null default false,
  arrived boolean not null default false,
  luggage_fallen boolean not null default false,
  obstacle_reason text,
  updated_at timestamptz not null default now()
);

-- Optional: keep updated_at fresh even if caller forgets
create or replace function public.set_updated_at_rover_live_state()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_set_updated_at_rover_live_state on public.rover_live_state;

create trigger trg_set_updated_at_rover_live_state
before update on public.rover_live_state
for each row
execute function public.set_updated_at_rover_live_state();

-- 2) Enable Row Level Security
alter table public.rover_live_state enable row level security;

-- 3) Remove old versions of the policies if they already exist
drop policy if exists "rover_live_state_select_authenticated" on public.rover_live_state;
drop policy if exists "rover_live_state_insert_authenticated" on public.rover_live_state;
drop policy if exists "rover_live_state_update_authenticated" on public.rover_live_state;

-- 4) Allow signed-in app users to read live rover state
create policy "rover_live_state_select_authenticated"
on public.rover_live_state
for select
to authenticated
using (true);

-- 5) Allow signed-in clients to insert live rover state
create policy "rover_live_state_insert_authenticated"
on public.rover_live_state
for insert
to authenticated
with check (true);

-- 6) Allow signed-in clients to update live rover state
create policy "rover_live_state_update_authenticated"
on public.rover_live_state
for update
to authenticated
using (true)
with check (true);



drop policy if exists "rover_live_state_select_anon" on public.rover_live_state;

create policy "rover_live_state_select_anon"
on public.rover_live_state
for select
to anon
using (true);




insert into public.rover_live_state (
  robot_id,
  mode,
  weight_kg,
  obstacle_hold,
  obstacle_avoid_active,
  arrived,
  luggage_fallen,
  obstacle_reason
)
values (
  'rover_01',
  'idle',
  0,
  false,
  false,
  false,
  false,
  null
)
on conflict (robot_id) do update
set updated_at = now();




alter table public.rover_live_state
add column if not exists obstacle_reason text;




insert into public.rover_live_state (
  robot_id,
  mode,
  weight_kg,
  obstacle_hold,
  obstacle_avoid_active,
  arrived,
  luggage_fallen,
  obstacle_reason
)
values (
  'rover_01',
  'idle',
  0,
  false,
  false,
  false,
  false,
  null
)
on conflict (robot_id) do update
set updated_at = now();



select column_name
from information_schema.columns
where table_name = 'rover_live_state';



insert into public.rover_live_state (
  robot_id,
  mode,
  weight_kg,
  obstacle_hold,
  obstacle_avoid_active,
  arrived,
  luggage_fallen,
  obstacle_reason
)
values (
  'rover_01',
  'idle',
  0,
  false,
  false,
  false,
  false,
  null
)
on conflict (robot_id) do update
set updated_at = now();


alter table rover_live_state
add column if not exists state text;




ALTER TABLE rover_commands
  ADD COLUMN IF NOT EXISTS luggage_weight_threshold_kg FLOAT DEFAULT 1.0,
  ADD COLUMN IF NOT EXISTS weight_alarm_override        BOOLEAN DEFAULT FALSE;



UPDATE rover_commands
SET
  luggage_weight_threshold_kg = 1.0,
  weight_alarm_override        = FALSE
WHERE robot_id = 'rover_01'
  AND luggage_weight_threshold_kg IS NULL;





SELECT
  *
FROM
  telemetry_snapshots
ORDER BY
  created_at DESC
LIMIT
  1;




create table if not exists obstacle_sensor_debug (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  robot_id text not null,
  state text,
  obstacle_reason text,
  obstacle_hold boolean default false,
  obstacle_avoid_active boolean default false,

  ultra_front_cm integer,
  ultra_left_cm integer,
  ultra_right_cm integer,
  ultra_back_cm integer,

  ema_front_cm integer,
  ema_left_cm integer,
  ema_right_cm integer,
  ema_back_cm integer,

  fast_front_cm integer,
  fast_left_cm integer,
  fast_right_cm integer,
  fast_back_cm integer
);

create index if not exists obstacle_sensor_debug_robot_created_idx
  on obstacle_sensor_debug (robot_id, created_at desc);





alter table obstacle_sensor_debug enable row level security;

-- Let authenticated users read debug rows
create policy "authenticated can read obstacle_sensor_debug"
on obstacle_sensor_debug
for select
to authenticated
using (true);

-- Optional: let anon users read too
-- Only add this if your Flutter app is truly using anon access for reads
create policy "anon can read obstacle_sensor_debug"
on obstacle_sensor_debug
for select
to anon
using (true);

-- Block app-side inserts
create policy "no anon insert obstacle_sensor_debug"
on obstacle_sensor_debug
for insert
to anon
with check (false);

create policy "no authenticated insert obstacle_sensor_debug"
on obstacle_sensor_debug
for insert
to authenticated
with check (false);

-- Block app-side updates
create policy "no anon update obstacle_sensor_debug"
on obstacle_sensor_debug
for update
to anon
using (false)
with check (false);

create policy "no authenticated update obstacle_sensor_debug"
on obstacle_sensor_debug
for update
to authenticated
using (false)
with check (false);

-- Block app-side deletes
create policy "no anon delete obstacle_sensor_debug"
on obstacle_sensor_debug
for delete
to anon
using (false);

create policy "no authenticated delete obstacle_sensor_debug"
on obstacle_sensor_debug
for delete
to authenticated
using (false);





SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'obstacle_sensor_debug';



alter table public.obstacle_sensor_debug enable row level security;

create policy "allow anon insert obstacle_sensor_debug"
on public.obstacle_sensor_debug
for insert
to anon
with check (true);



alter table public.obstacle_sensor_debug enable row level security;

create policy "allow obstacle_sensor_debug select"
on public.obstacle_sensor_debug
for select
to anon
using (true);




CREATE POLICY "Allow rover upsert"
ON rover_live_state
FOR ALL
USING (true)
WITH CHECK (true);




alter table telemetry_snapshots
add column if not exists luggage_weight_threshold_kg double precision;


alter table telemetry_snapshots
add column if not exists luggage_fallen boolean;


alter table rover_commands
add column if not exists recalibrate_luggage boolean;

alter table rover_live_state
add column if not exists source_ts_iso timestamptz,
add column if not exists db_written_at timestamptz;

alter table telemetry_snapshots
add column if not exists source_ts_iso timestamptz,
add column if not exists db_written_at timestamptz;






create table rover_gps_samples (
  id bigint generated always as identity primary key,
  robot_id text not null,
  latitude double precision not null,
  longitude double precision not null,
  heading_deg double precision,
  ts timestamptz not null default now()
);
