-- BigQuery Standard SQL
-- Top 10% (0.10) and top 100 by reach are inlined so this is a single
-- query job. DECLARE would make the Python client submit a script, which
-- sits in RUNNING with no row progress until every statement finishes.

WITH syndicated_segments AS (
  SELECT
    dms_segment_id,
    segment_name,
    segment_description,
    segment_type,
    seller_customer_id,
    field_id,
    value_id,
    segment_enabled
  FROM `liveramp-eng-pie.entities.fin_marketplace_segments`
  WHERE segment_type = 'Syndicated'
    AND segment_enabled = TRUE
    AND dms_segment_id IS NOT NULL
),

/* Current active distribution footprint */
active_distribution AS (
  SELECT
    s.dms_segment_id,

    COUNT(DISTINCT das.destination_account_id)
      AS active_destination_accounts,

    COUNT(DISTINCT da.customer_id)
      AS active_buyers,

    COUNT(DISTINCT da.platform_customer_id)
      AS active_platforms,

    STRING_AGG(
      DISTINCT platform_customer.customer_name,
      ', '
      ORDER BY platform_customer.customer_name
    ) AS active_platform_names

  FROM `liveramp-eng-pie.entities.fin_connect_destination_account_segments` das

  INNER JOIN syndicated_segments s
    ON das.field_id = s.field_id
   AND das.value_id = s.value_id

  INNER JOIN `liveramp-eng-pie.entities.fin_connect_destination_accounts` da
    ON das.destination_account_id = da.destination_account_id

  INNER JOIN `liveramp-eng-pie.entities.fin_connect_customers` platform_customer
    ON da.platform_customer_id = platform_customer.customer_id

  WHERE das.segment_status = 'Enabled'
    AND da.status_value = 'Active'

    -- Exclude the seller distributing its own segment
    AND da.customer_id <> s.seller_customer_id

  GROUP BY
    s.dms_segment_id
),

/* Connect overall estimated reach */
reach_metrics AS (
  SELECT
    dms_segment_id,

    MAX(
      IF(ad_network_account_id = 2508, estimated_reach, NULL)
    ) AS cookie_reach,

    MAX(
      IF(ad_network_account_id = 6778, estimated_reach, NULL)
    ) AS ios_reach,

    MAX(
      IF(ad_network_account_id = 21906, estimated_reach, NULL)
    ) AS android_reach,

    MAX(
      IF(ad_network_account_id IS NULL, input_records, NULL)
    ) AS input_records,

    MAX(
      IF(ad_network_account_id = 2508, updated_at, NULL)
    ) AS cookie_reach_updated_at,

    MAX(
      IF(ad_network_account_id = 6778, updated_at, NULL)
    ) AS ios_reach_updated_at,

    MAX(
      IF(ad_network_account_id = 21906, updated_at, NULL)
    ) AS android_reach_updated_at

  FROM `corp-bi-us-prod.rldb.dms_segment_stats`

  WHERE ad_network_account_id IN (2508, 6778, 21906)
     OR ad_network_account_id IS NULL

  GROUP BY
    dms_segment_id
),

/*
  Platform-specific Connect cookie reach.

  Connect calculates:
  base cookie reach * platform cookie overlap percentage
*/
connect_platform_reach AS (
  SELECT
    dms_segment_id,
    STRING_AGG(
      platform_reach_label,
      '; '
      ORDER BY platform_name
    ) AS reach_by_platform
  FROM (
    SELECT DISTINCT
      r.dms_segment_id,
      platform_customer.customer_name AS platform_name,
      CONCAT(
        platform_customer.customer_name,
        ': ',
        CAST(
          ROUND(
            r.cookie_reach * platform.cookie_overlap_percentage
          ) AS STRING
        )
      ) AS platform_reach_label
    FROM reach_metrics r
    INNER JOIN `liveramp-eng-pie.entities.fin_marketplace_platforms` platform
      ON platform.is_connect_enabled = TRUE
     AND platform.is_data_store_enabled = TRUE
     AND platform.is_stats_visible = TRUE
     AND platform.cookie_overlap_percentage IS NOT NULL
    INNER JOIN `liveramp-eng-pie.entities.fin_connect_customers` platform_customer
      ON platform.platform_customer_id = platform_customer.customer_id
    WHERE r.cookie_reach IS NOT NULL
  )
  GROUP BY
    dms_segment_id
),

/* Combine segment, distribution, and reach metrics */
segment_metrics AS (
  SELECT
    s.dms_segment_id,
    s.segment_name,
    s.segment_description,
    s.segment_type,
    s.seller_customer_id,

    COALESCE(d.active_destination_accounts, 0)
      AS active_destination_accounts,

    COALESCE(d.active_buyers, 0)
      AS active_buyers,

    COALESCE(d.active_platforms, 0)
      AS active_platforms,

    COALESCE(d.active_platform_names, '')
      AS active_platform_names,

    COALESCE(r.cookie_reach, 0)
      AS cookie_reach,

    COALESCE(r.ios_reach, 0)
      AS ios_reach,

    COALESCE(r.android_reach, 0)
      AS android_reach,

    COALESCE(r.input_records, 0)
      AS input_records,

    r.cookie_reach_updated_at,
    r.ios_reach_updated_at,
    r.android_reach_updated_at,

    COALESCE(p.reach_by_platform, '')
      AS reach_by_platform

  FROM syndicated_segments s

  LEFT JOIN active_distribution d
    USING (dms_segment_id)

  LEFT JOIN reach_metrics r
    USING (dms_segment_id)

  LEFT JOIN connect_platform_reach p
    USING (dms_segment_id)
),

/* Rank segments */
ranked_segments AS (
  SELECT
    *,

    COUNT(*) OVER ()
      AS total_segments,

    DENSE_RANK() OVER (
      ORDER BY
        active_destination_accounts DESC,
        active_buyers DESC,
        dms_segment_id
    ) AS distribution_rank,

    DENSE_RANK() OVER (
      ORDER BY
        cookie_reach DESC,
        ios_reach DESC,
        android_reach DESC,
        dms_segment_id
    ) AS reach_rank

  FROM segment_metrics
),

/* Apply the top-10% and top-100 definitions */
classified_segments AS (
  SELECT
    *,

    (
      active_destination_accounts > 0
      AND distribution_rank <= CEIL(total_segments * 0.10)
    ) AS is_highly_distributed,

    (
      (
        cookie_reach > 0
        OR ios_reach > 0
        OR android_reach > 0
      )
      AND reach_rank <= CEIL(total_segments * 0.10)
    ) AS is_highly_reachable,

    (
      (
        cookie_reach > 0
        OR ios_reach > 0
        OR android_reach > 0
      )
      AND reach_rank <= 100
    ) AS is_top_n_by_reach

  FROM ranked_segments
)

SELECT
  dms_segment_id,
  segment_name,
  segment_description,
  segment_type,
  seller_customer_id,

  active_destination_accounts,
  active_buyers,
  active_platforms,
  active_platform_names,

  cookie_reach,
  ios_reach,
  android_reach,
  input_records,

  cookie_reach_updated_at,
  ios_reach_updated_at,
  android_reach_updated_at,

  reach_by_platform,

  distribution_rank,
  reach_rank,

  is_highly_distributed,
  is_highly_reachable,
  is_top_n_by_reach

FROM classified_segments

WHERE is_highly_distributed
   OR is_highly_reachable
   OR is_top_n_by_reach

ORDER BY
  is_highly_distributed DESC,
  is_highly_reachable DESC,
  reach_rank ASC,
  distribution_rank ASC;
