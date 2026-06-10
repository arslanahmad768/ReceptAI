-- ──────────────────────────────────────────────────────────────────────────────
-- 001_initial_schema.sql
-- Run in: Supabase Dashboard → SQL Editor → Run
-- ──────────────────────────────────────────────────────────────────────────────

-- Profiles (app-level user record)
create table if not exists profiles (
  id            uuid primary key default gen_random_uuid(),
  email         text unique not null,
  password_hash text not null,
  created_at    timestamptz default now()
);

-- Niches (dental | medical | legal | spa | ...)
create table if not exists niches (
  id         uuid primary key default gen_random_uuid(),
  slug       text unique not null,
  label      text not null,
  created_at timestamptz default now()
);
insert into niches (slug, label) values
  ('dental', 'Dental Clinics')
on conflict do nothing;

-- Scrape jobs (created manually OR by a cron trigger)
create table if not exists scrape_jobs (
  id           uuid primary key default gen_random_uuid(),
  niche_id     uuid references niches(id),
  search_term  text not null,
  location     text not null,
  limit_count  int default 10,
  status       text default 'pending',  -- pending|running|done|failed
  error_msg    text,
  triggered_by text default 'manual',  -- manual|cron
  created_by   uuid references profiles(id),
  created_at   timestamptz default now(),
  finished_at  timestamptz
);

-- Clinics (Stage 1 output)
create table if not exists clinics (
  id              uuid primary key default gen_random_uuid(),
  job_id          uuid references scrape_jobs(id) on delete cascade,
  niche_id        uuid references niches(id),
  name            text not null,
  address         text,
  phone           text,
  website         text,
  google_maps_url text unique,
  rating          numeric(3,1),
  review_count    int,
  reviews_json    jsonb,
  created_at      timestamptz default now()
);

-- Qualifications (Stage 2 output)
create table if not exists qualifications (
  id                   uuid primary key default gen_random_uuid(),
  clinic_id            uuid unique references clinics(id) on delete cascade,
  qualification_score  int,
  primary_pain_point   text,
  complaint_quote      text,
  personalization_hook text,
  recommend_outreach   boolean default false,
  model_used           text,
  created_at           timestamptz default now()
);

-- Leads (Stage 3 output — clinic + best email)
create table if not exists leads (
  id            uuid primary key default gen_random_uuid(),
  clinic_id     uuid unique references clinics(id) on delete cascade,
  email         text,
  email_method  text,  -- html_direct|smtp_verified|whois_registrant|apollo|browser_scraped
  all_emails    jsonb,
  doctors_found jsonb,
  status        text default 'new',  -- new|contacted|replied|converted|rejected
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);

-- Cron job schedules (managed via API)
create table if not exists cron_jobs (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  niche_id     uuid references niches(id),
  location     text not null,
  limit_count  int default 10,
  cron_expr    text not null,   -- standard 5-part cron: "0 9 * * 1" = Mon 9am
  is_active    boolean default true,
  last_run_at  timestamptz,
  next_run_at  timestamptz,
  created_by   uuid references profiles(id),
  created_at   timestamptz default now()
);

-- Email templates (per niche, seeded below)
create table if not exists email_templates (
  id         uuid primary key default gen_random_uuid(),
  niche_id   uuid references niches(id),
  name       text not null,        -- "pain_hook" | "demo_invite" | "followup"
  subject    text not null,
  body       text not null,
  is_active  boolean default true,
  created_at timestamptz default now()
);

-- Outreach campaigns
create table if not exists campaigns (
  id         uuid primary key default gen_random_uuid(),
  name       text not null,
  niche_id   uuid references niches(id),
  created_by uuid references profiles(id),
  status     text default 'draft',  -- draft|active|paused|completed
  created_at timestamptz default now()
);

-- Individual outreach emails (one row per lead per campaign)
create table if not exists outreach_emails (
  id                uuid primary key default gen_random_uuid(),
  campaign_id       uuid references campaigns(id) on delete cascade,
  lead_id           uuid references leads(id),
  to_email          text not null,
  subject           text not null,
  body              text not null,
  status            text default 'draft',  -- draft|queued|sent|bounced|replied|failed
  resend_message_id text,
  sent_at           timestamptz,
  opened_at         timestamptz,
  replied_at        timestamptz,
  created_at        timestamptz default now()
);

-- ── RLS: all tables locked to service role (our API) ─────────────────────────
alter table profiles        enable row level security;
alter table niches          enable row level security;
alter table scrape_jobs     enable row level security;
alter table clinics         enable row level security;
alter table qualifications  enable row level security;
alter table leads           enable row level security;
alter table cron_jobs       enable row level security;
alter table email_templates enable row level security;
alter table campaigns       enable row level security;
alter table outreach_emails enable row level security;

create policy "service_all" on profiles        for all using (true);
create policy "service_all" on niches          for all using (true);
create policy "service_all" on scrape_jobs     for all using (true);
create policy "service_all" on clinics         for all using (true);
create policy "service_all" on qualifications  for all using (true);
create policy "service_all" on leads           for all using (true);
create policy "service_all" on cron_jobs       for all using (true);
create policy "service_all" on email_templates for all using (true);
create policy "service_all" on campaigns       for all using (true);
create policy "service_all" on outreach_emails for all using (true);

-- ── Seed professional email templates for dental niche ────────────────────────
insert into email_templates (niche_id, name, subject, body) values
(
  (select id from niches where slug = 'dental'),
  'pain_hook',
  'Quick question about {{clinic_name}}''s missed calls',
  'Hi {{first_name}},

{{personalization_hook}}

I''m reaching out because we built ReceptAI — an AI phone receptionist that answers every call your team can''t, 24/7. It books appointments, handles common patient questions, and never puts anyone on hold.

The practices we work with typically recover 15–25 missed patient calls per week within the first month.

I''d love to show you how it would sound answering calls for {{clinic_name}} specifically.

👉 Try the live demo right now (takes 90 seconds): {{demo_url}}
📅 Or book a free 15-min walkthrough: {{booking_url}}

No contract. No setup fee. Worth a quick listen?

Best,
{{sender_name}}
ReceptAI — AI Receptionist for Dental Practices'
),
(
  (select id from niches where slug = 'dental'),
  'demo_invite',
  'We built a voice AI demo for {{clinic_name}} — 90 sec to try',
  'Hi {{first_name}},

Quick one.

We built an AI phone receptionist for dental practices like {{clinic_name}}. It answers like a real receptionist, handles appointment bookings, after-hours questions, and — most importantly — never misses a call.

Call our demo line right now and hear it yourself: {{demo_phone}}

Or watch a 2-minute walkthrough here: {{demo_url}}

If you like what you hear, I can configure it specifically for {{clinic_name}} at no cost for the first 30 days.

Worth 90 seconds?

{{sender_name}}
ReceptAI'
),
(
  (select id from niches where slug = 'dental'),
  'followup',
  'Re: {{clinic_name}} — did you get to try the demo?',
  'Hi {{first_name}},

Following up quickly on my last note.

I know front-desk teams are slammed. So here''s the one thing to know:

Every call {{clinic_name}} misses is a patient who books with the practice down the street instead.

ReceptAI catches every one of them — automatically.

One line to hear it live: {{demo_phone}}

Happy to set up a free pilot this week — no contract, cancel any time.

{{sender_name}}'
)
on conflict do nothing;
