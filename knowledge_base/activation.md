# Activation

## Overview

Activation is the process of making a LiveRamp syndicated (third-party) segment available for media buying on a destination platform. A segment is not "live" for a buyer until it has been activated to a **destination account** and delivery has produced identifiers the platform can address.

Activation sits between the Data Marketplace catalog and the platform's audience tools (for example The Trade Desk or Google DV360). Connect is the product surface where buyers and sellers configure destination accounts, matching, and delivery.

## Matching actions and SSA

LiveRamp matching actions translate seller-side identifiers into platform-addressable keys. **SSA** (Safe Haven / syndicated segment activation workflows in Connect) governs how a syndicated field/value pair is authorized for a given destination.

Typical matching steps:

1. Resolve the syndicated segment to its `field_id` / `value_id` pair.
2. Apply the seller's distribution rules and any buyer entitlements.
3. Run identifier matching (cookies, mobile IDs, CIDs) according to the destination's allowed identifier types.
4. Emit a **digest** of matched keys for delivery.

If matching yields no overlap, the destination will show zero addressable reach even when Connect **cookie_reach** is non-zero. Cookie reach is an estimate of LiveRamp's cookie graph for the segment, not a guarantee of platform-matched IDs.

## Digest

A **digest** is the packaged output of matching: the set of identifiers (and metadata) that will be delivered to a destination. Digests are scoped to a destination account and a delivery mode. Operators inspect digest size when diagnosing "segment enabled but nothing delivered" tickets.

Digest volume should be compared to:

- `input_records` — records LiveRamp ingested for the segment
- `cookie_reach` / `ios_reach` / `android_reach` — graph estimates by identifier class
- Platform cookie overlap — Connect cookie reach multiplied by the platform's `cookie_overlap_percentage`

## Delivery modes

### FULL delivery

**FULL** delivery sends the complete current matched universe for the segment to the destination. Use FULL for first-time activation and whenever the buyer needs a complete refresh (for example after a taxonomy change). FULL jobs are larger and more expensive than incremental updates.

### INCREMENTAL delivery

**INCREMENTAL** delivery sends only net-new or dropped identifiers since the previous successful digest. Incremental mode keeps destinations in sync without re-sending the entire audience. If a destination was never initialized with FULL, incremental-only delivery can look empty.

## AIM mapping

**AIM mapping** (Account Identifier Mapping) binds a Connect destination account to the platform's advertiser or seat identifiers. Without a valid AIM mapping, matching may succeed internally while the platform rejects or orphans the delivered audience.

When a buyer reports that a syndicated segment is "not showing in the UI", verify AIM mapping, destination account status (`Active`), and segment status on the destination account segment row (`Enabled`) before assuming a catalog or SQL ranking issue.
