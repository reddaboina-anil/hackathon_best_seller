# Platforms

## Overview

LiveRamp syndicated segments activate onto demand-side and publisher platforms. Two of the most common Connect destinations are **The Trade Desk (TTD)** and **Google DV360**. Platform-specific cookie reach is not the same as Connect's overall cookie graph estimate.

## Connect versus Data Store

- **Connect platform**: the LiveRamp product used to configure destination accounts, matching, delivery modes, and entitlements. Bestsellers distribution metrics are Connect-centric (enabled destination-account segments, active accounts).
- **Data Marketplace**: the commercial catalog where syndicated segments are listed, priced, and entitled. A segment can exist in the marketplace catalog and still have zero Connect distribution if no buyer has activated it.
- **Data Store**: platform-side or LiveRamp data-store flags (`is_data_store_enabled`) used when computing which platforms should show stats and cookie overlap. Bestsellers platform reach requires Connect enabled, Data Store enabled, stats visible, and a non-null `cookie_overlap_percentage`.

## Platform cookie overlap

Connect estimates **platform cookie reach** as:

`base cookie_reach * platform.cookie_overlap_percentage`

The overlap percentage is a platform property, not a per-segment measurement. Two platforms can show very different addressable cookie estimates for the same syndicated segment even when Connect `cookie_reach` is identical.

`reach_by_platform` in the bestsellers query concatenates `platform_name: estimated_cookies` pairs for reporting.

## The Trade Desk (TTD)

TTD destination accounts must be Active, with the syndicated segment Enabled on that destination account. Cookie overlap for TTD is applied through the marketplace platforms table, not by hard-coding a TTD account id in reach_metrics. Reach_metrics uses LiveRamp ad-network account ids for identifier classes (cookies, iOS, Android), which are not TTD seat ids.

## Google DV360

DV360 follows the same Connect destination-account model. UI visibility depends on AIM mapping and the platform's audience tools. DV360 cookie overlap is also sourced from `fin_marketplace_platforms` when the platform row is Connect-enabled, Data Store-enabled, and stats-visible.

## Diagnosing platform gaps

If overall cookie_reach is high but a platform shows near-zero estimated cookies, inspect `cookie_overlap_percentage` and whether that platform is included in `is_stats_visible`. If overlap is fine but the buyer still sees nothing, inspect activation (digest, FULL vs INCREMENTAL, AIM mapping) rather than the bestsellers rank.
