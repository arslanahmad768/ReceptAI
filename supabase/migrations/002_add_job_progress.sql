-- 002_add_job_progress.sql
-- Adds real-time progress tracking to scrape_jobs.
-- Run in: Supabase Dashboard → SQL Editor → Run

alter table scrape_jobs
  add column if not exists progress jsonb not null default '{}';
