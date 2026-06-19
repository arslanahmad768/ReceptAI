-- 003_campaigns_grid.sql
-- Run in: Supabase Dashboard → SQL Editor → Run

-- City bounding boxes (auto-populated via Nominatim, never edited manually)
create table if not exists locations (
  id          uuid primary key default gen_random_uuid(),
  country     text not null,
  city        text not null,
  bbox_north  float not null,
  bbox_south  float not null,
  bbox_east   float not null,
  bbox_west   float not null,
  created_at  timestamptz default now(),
  unique (country, city)
);

-- Individual coordinate search cells within a city
create table if not exists grid_cells (
  id          uuid primary key default gen_random_uuid(),
  location_id uuid references locations(id) on delete cascade,
  center_lat  float not null,
  center_lng  float not null,
  radius_km   float not null,
  depth       int not null default 0,
  parent_id   uuid references grid_cells(id),
  created_at  timestamptz default now()
);

-- One campaign = one niche scrape across an entire city
create table if not exists scrape_campaigns (
  id                uuid primary key default gen_random_uuid(),
  niche_id          uuid references niches(id),
  location_id       uuid references locations(id),
  status            text not null default 'pending',
  initial_radius_km float not null default 1.0,
  max_depth         int not null default 4,
  clinics_per_cell  int not null default 20,
  created_by        uuid references profiles(id),
  created_at        timestamptz default now()
);

-- Per-cell tracking within a campaign
create table if not exists campaign_cells (
  id             uuid primary key default gen_random_uuid(),
  campaign_id    uuid references scrape_campaigns(id) on delete cascade,
  cell_id        uuid references grid_cells(id),
  scrape_job_id  uuid references scrape_jobs(id),
  status         text not null default 'pending',
  result_count   int,
  created_at     timestamptz default now()
);
