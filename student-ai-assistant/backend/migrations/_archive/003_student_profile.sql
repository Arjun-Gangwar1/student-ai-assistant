-- ============================================================
-- Migration 003: Student profile fields (year + branch)
-- Run this in Supabase SQL Editor
-- ============================================================

ALTER TABLE students
  ADD COLUMN IF NOT EXISTS year   SMALLINT CHECK (year BETWEEN 1 AND 6),
  ADD COLUMN IF NOT EXISTS branch TEXT;

COMMENT ON COLUMN students.year   IS '1=first year, 2=second, etc.';
COMMENT ON COLUMN students.branch IS 'e.g. CS, EE, ME, CE, PH, MA';
