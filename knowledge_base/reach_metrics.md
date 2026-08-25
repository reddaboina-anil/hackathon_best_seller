# Reach metrics

## Overview

Reach metrics in the bestsellers pipeline come from `corp-bi-us-prod.rldb.dms_segment_stats`. They are **estimates of LiveRamp identifier graphs**, not billed impressions and not platform-matched keys.

Identifier classes are selected with `ad_network_account_id`:

| ad_network_account_id | Metric |
|---|---|
| 2508 | `cookie_reach` |
| 6778 | `ios_reach` |
| 21906 | `android_reach` |
| NULL | `input_records` |

## cookie_reach

**cookie_reach** is the maximum estimated cookie-graph reach for the segment on ad network account 2508. It is the primary ranking input for `reach_rank` (along with iOS and Android as tie-breakers). Zero cookie_reach with non-zero mobile reach can still qualify a segment as highly reachable.

Do not present cookie_reach as "people who will see ads on TTD". Convert to platform estimates using cookie overlap when the user asks about a named platform.

## ios_reach and android_reach

**ios_reach** (6778) and **android_reach** (21906) are mobile identifier graph estimates. Privacy and identifier availability make these series move independently from cookies. Rankings use cookie first, then iOS, then Android, then `dms_segment_id` for stability.

## input_records

**input_records** is the ingested record count associated with a null ad_network_account_id in segment stats. It is a data-volume signal, not an addressable audience. A high input_records / low cookie_reach pattern often means identifiers did not match the cookie graph.

## Timestamps

`cookie_reach_updated_at`, `ios_reach_updated_at`, and `android_reach_updated_at` are the stats timestamps for each identifier class. Stale timestamps should be mentioned when ranking "top by reach" if the user cares about freshness.

## Ranking flags

- `reach_rank` — dense rank by cookie, iOS, Android, then id
- `is_highly_reachable` — some reach > 0 and rank within the top 10% of all syndicated segments in the query
- `is_top_n_by_reach` — some reach > 0 and rank ≤ 100 (configurable `top_n` in SQL)
