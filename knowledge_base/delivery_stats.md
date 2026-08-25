# Delivery stats

## Overview

Delivery statistics describe what actually landed on destinations after matching, as opposed to graph **reach** estimates. Connect destination-account segment rows (`fin_connect_destination_account_segments`) carry `segment_status`. Destination accounts (`fin_connect_destination_accounts`) carry `status_value`.

Bestsellers **active distribution** requires:

- `das.segment_status = 'Enabled'`
- `da.status_value = 'Active'`
- buyer `da.customer_id <> seller_customer_id`

## num_total_audience_keys and related fields

Platform or Connect delivery stats may expose fields such as `num_total_audience_keys` (count of keys in the latest digest). Treat these as delivery facts, not as `cookie_reach`. A segment can rank highly on cookie_reach and still have low `num_total_audience_keys` on a given destination if matching overlap is thin.

When answering "how many keys were delivered to TTD for segment X", prefer live destination-level stats over the catalog embedding.

## Destination account

A **destination_account** is the Connect object that binds a buyer (or operator) to a specific platform seat. Distribution metrics count **distinct** `destination_account_id` values. One buyer may have many destination accounts across platforms; one platform may have many accounts.

`active_destination_accounts`, `active_buyers`, and `active_platforms` are independent counts. High destination-account count with low buyer count usually means one buyer activated many seats.

## Segment status versus catalog enabled

`segment_enabled` on the marketplace segment means the taxonomy node is available to sell. `segment_status = 'Enabled'` on a destination-account segment means that node is turned on for that seat. Bestsellers distribution uses the latter. Always distinguish catalog enablement from destination enablement in answers.
