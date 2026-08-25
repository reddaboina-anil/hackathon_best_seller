# Distribution

## Overview

**Distribution** measures how widely a syndicated segment is activated in Connect, independent of graph reach. A segment can be highly reachable (large cookie graph) and poorly distributed (few buyers), or the reverse (niche reach, many seats).

Bestsellers `distribution_rank` is a dense rank over `active_destination_accounts` descending, then `active_buyers` descending, then `dms_segment_id`.

## destination_account

See delivery stats: each **destination_account** is one Connect seat binding. `active_destination_accounts` is the distinct count of those ids for enabled segments on active accounts, excluding the seller.

## active_buyers

**active_buyers** counts distinct `da.customer_id` values (Connect customers who are not the seller). Use this when the user asks "how many companies bought this" rather than "how many seats".

## active_platforms and names

**active_platforms** counts distinct `platform_customer_id` values. `active_platform_names` is a comma-separated, ordered list of platform customer names. Use names when answering "is this on TTD and DV360?".

## distribution_rank versus reach_rank

Never interchange the two ranks:

- **distribution_rank** — commercial footprint in Connect
- **reach_rank** — identifier-graph size

`is_highly_distributed` requires `active_destination_accounts > 0` and distribution_rank within the top 10% (`top_percent`). A segment with rank 1 on reach and no destination accounts is not highly distributed.

## Bestsellers output filter

The bestsellers SQL returns segments that are highly distributed, highly reachable, **or** top-N by reach. The union is intentional: operators can inspect commercial hits, graph hits, and the explicit top-100 reach list in one table. When the user asks only for "top by cookie reach", filter or order by `reach_rank` / `cookie_reach`, not `distribution_rank`.
