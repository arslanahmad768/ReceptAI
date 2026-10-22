-- 004_hours_and_booking.sql
-- Run in: Supabase Dashboard → SQL Editor → Run

-- Opening hours (Stage 1 — Google Maps) + derived after-hours-coverage gap
alter table clinics
  add column if not exists hours_json     jsonb,   -- {"Monday": "8 AM to 5 PM", "Saturday": "Closed", ...}
  add column if not exists after_hours_gap boolean; -- true if closed weekends or closes before 6pm on a weekday; null = not enough hours data

-- Online booking detection (Stage 3 — website scan, no extra requests)
alter table leads
  add column if not exists has_online_booking boolean,
  add column if not exists booking_software    text; -- e.g. "Zocdoc", "NexHealth"; null if generic/unknown widget or none found
