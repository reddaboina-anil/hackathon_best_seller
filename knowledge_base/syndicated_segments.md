# Syndicated segments

## Overview

**Syndicated segments** (also called **3P segments**) are third-party audiences sold through LiveRamp's Data Marketplace. A seller packages a taxonomy node; buyers activate that node to destination platforms. Syndicated segments are distinct from first-party (1P) client data and from custom modeled audiences.

In BigQuery they appear in `liveramp-eng-pie.entities.fin_marketplace_segments` with `segment_type = 'Syndicated'` and `segment_enabled = TRUE`.

## Seller and buyer model

- **Seller**: the data provider who owns the taxonomy. Identified by `seller_customer_id`. A seller distributing its own segment to its own destination accounts is excluded from "active buyer" counts in bestsellers logic.
- **Buyer**: a customer who entitles and activates the segment onto one or more destination accounts.
- **Platform**: the media destination (TTD, DV360, etc.), represented as a Connect customer (`platform_customer_id`).

Active distribution footprint counts distinct destination accounts, distinct buyer customers, and distinct platforms — never counting the seller as a buyer of their own segment.

## field_id and value_id

Every syndicated segment is addressed in Connect delivery tables by the pair **`field_id` / `value_id`**, not by name. `dms_segment_id` is the catalog key used in stats and ranking. Joins from marketplace segments to destination-account segments use `field_id` and `value_id`.

If a join on `dms_segment_id` alone returns no distribution rows, check that field/value ids still match the enabled destination-account segment rows.

## Catalog text versus live metrics

The vector **segment_catalog** stores name, id, and description only. Numeric reach, ranks, and buyer counts are **never** stored in Qdrant. Always query BigQuery (the bestsellers SQL) for current `cookie_reach`, `distribution_rank`, `reach_rank`, and related flags (`is_highly_distributed`, `is_highly_reachable`, `is_top_n_by_reach`).

## Deconfliction (AMC)

**Deconfliction (AMC)** refers to Amazon Marketing Cloud (and similar clean-room) rules that prevent double-counting overlapping third-party audiences. When comparing reach across overlapping syndicated segments, do not add cookie_reach figures together; treat them as overlapping estimates unless a deconflicted measurement is available.
