-- Tag computation for the standalone tag-api.
-- Executed by `docker compose --profile compute run --rm tag-compute`.
-- Input: tab-separated BigQuery export at /workspace/csv_dump/best_sellers_output.csv
-- Output tables in tags.duckdb: segment_dump, tag_definitions, tagged,
-- segment_tag_assignments, tag_segment_index, thresholds.

-- Step 1: Persist the BigQuery export (tab- or comma-separated; header required).
-- Named segment_dump (not raw): DuckDB's DROP VIEW IF EXISTS errors when a TABLE
-- of the same name exists, and CREATE OR REPLACE TABLE errors when a VIEW exists.
-- This name has only ever been a table, so CREATE OR REPLACE TABLE is always safe.
CREATE OR REPLACE TABLE segment_dump AS
SELECT * FROM read_csv_auto(
  '/workspace/csv_dump/best_sellers_output.csv',
  header=true
);

-- Step 2: Compute p90/p99/p80 thresholds in a single pass
-- Only columns that exist in best_sellers.sql output
CREATE OR REPLACE TABLE thresholds AS
SELECT
  PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY ios_reach)
    FILTER (WHERE ios_reach > 0)        AS ios_reach_p90,
  PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY android_reach)
    FILTER (WHERE android_reach > 0)    AS android_reach_p90,
  PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY cookie_reach)
    FILTER (WHERE cookie_reach > 0)     AS cookie_reach_p99,
  PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY active_buyers)
                                        AS buyers_p90,
  PERCENTILE_CONT(0.80) WITHIN GROUP (ORDER BY ios_reach)
    FILTER (WHERE ios_reach > 0)        AS ios_p80,
  PERCENTILE_CONT(0.80) WITHIN GROUP (ORDER BY android_reach)
    FILTER (WHERE android_reach > 0)    AS android_p80,
  PERCENTILE_CONT(0.80) WITHIN GROUP (ORDER BY cookie_reach)
    FILTER (WHERE cookie_reach > 0)     AS cookie_p80
FROM segment_dump;

-- Step 3: Evaluate all 11 tag rules per segment
-- active_platform_names format: "Facebook, MNTN, The Trade Desk" (comma-space)
CREATE OR REPLACE TABLE tagged AS
SELECT
  r.dms_segment_id AS segment_id,
  -- Platform Activation
  (list_contains(string_split(r.active_platform_names, ', '), 'Facebook')
    AND r.is_highly_distributed)                             AS top_facebook_activated,
  (list_contains(string_split(r.active_platform_names, ', '), 'The Trade Desk')
    AND r.is_highly_distributed)                             AS top_ttd_activated,
  (list_contains(string_split(r.active_platform_names, ', '), 'Google | Data Marketplace')
    AND r.is_highly_distributed)                             AS top_google_activated,
  (r.active_platforms >= 5)                                  AS multi_platform_powerhouse,
  -- Reach
  (r.ios_reach     >= t.ios_reach_p90)                       AS high_ios_reach,
  (r.android_reach >= t.android_reach_p90)                   AS high_android_reach,
  (r.cookie_reach  >= t.cookie_reach_p99)                    AS massive_cookie_scale,
  (r.ios_reach     >= t.ios_p80
    AND r.android_reach >= t.android_p80
    AND r.cookie_reach  >= t.cookie_p80)                     AS cross_device_champion,
  -- Distribution
  r.is_highly_distributed                                     AS highly_distributed,
  (r.active_buyers >= t.buyers_p90)                          AS buyer_magnet,
  (r.active_platforms >= 4)                                  AS broad_platform_breadth
FROM segment_dump r CROSS JOIN thresholds t;

-- Step 4: Write tag_definitions (11 rows — only tags computable from best_sellers.sql)
CREATE OR REPLACE TABLE tag_definitions AS
SELECT * FROM (VALUES
  ('top_facebook_activated',   'Top Facebook Activated',    'Top 10% distributed on Facebook',               'platform', 1),
  ('top_ttd_activated',        'Top TTD Activated',         'Top 10% distributed on The Trade Desk',         'platform', 2),
  ('top_google_activated',     'Top Google Activated',      'Top 10% on Google | Data Marketplace',          'platform', 3),
  ('multi_platform_powerhouse','Multi-Platform Powerhouse', 'Active on 5+ ad platforms',                     'platform', 4),
  ('high_ios_reach',           'High iOS Reach',            'Top 10% by iOS device reach',                   'reach',    5),
  ('high_android_reach',       'High Android Reach',        'Top 10% by Android device reach',               'reach',    6),
  ('massive_cookie_scale',     'Massive Cookie Scale',      'Top 1% by cookie reach',                        'reach',    7),
  ('cross_device_champion',    'Cross-Device Champion',     'Top 20% on cookie, iOS, and Android reach',     'reach',    8),
  ('highly_distributed',       'Highly Distributed',        'Top 10% by destination accounts',               'distribution', 9),
  ('buyer_magnet',             'Buyer Magnet',              'Top 10% by active buyers',                      'distribution', 10),
  ('broad_platform_breadth',   'Broad Platform Breadth',    'Active on 4+ ad platforms',                     'distribution', 11)
) t(tag_key, display_name, description, category, priority);

-- Step 5: Unpivot to segment_tag_assignments (one row per segment-tag pair)
CREATE OR REPLACE TABLE segment_tag_assignments AS
SELECT segment_id, tag_key, 1.0 AS score, current_timestamp::VARCHAR AS computed_at
FROM tagged
UNPIVOT (is_tagged FOR tag_key IN (
  top_facebook_activated, top_ttd_activated, top_google_activated, multi_platform_powerhouse,
  high_ios_reach, high_android_reach, massive_cookie_scale, cross_device_champion,
  highly_distributed, buyer_magnet, broad_platform_breadth
))
WHERE is_tagged = true;

-- Step 6: Build inverted index (tag → list of segment IDs)
CREATE OR REPLACE TABLE tag_segment_index AS
SELECT tag_key, segment_id, score FROM segment_tag_assignments;

CREATE INDEX IF NOT EXISTS idx_dump_id ON segment_dump(dms_segment_id);
CREATE INDEX IF NOT EXISTS idx_sta_seg ON segment_tag_assignments(segment_id);
CREATE INDEX IF NOT EXISTS idx_tsi_tag ON tag_segment_index(tag_key, score);
