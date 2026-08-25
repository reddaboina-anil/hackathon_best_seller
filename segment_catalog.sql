-- Catalog ingest only: names and descriptions for Qdrant segment_catalog.
-- Live reach / distribution stay in best_sellers.sql and run at query time.
-- The Python client appends ORDER BY dms_segment_id LIMIT/OFFSET per page.

SELECT
  dms_segment_id,
  seller_customer_id,
  segment_name,
  segment_description
FROM `liveramp-eng-pie.entities.fin_marketplace_segments`
WHERE segment_type = 'Syndicated'
  AND segment_enabled = TRUE
  AND dms_segment_id IS NOT NULL
;