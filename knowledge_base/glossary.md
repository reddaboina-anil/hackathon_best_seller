# Glossary

Authoritative definitions for LiveRamp syndicated-segment operations. Each H2 heading is the canonical term.

## activation

The process of enabling a syndicated segment on a Connect destination account so identifiers can be matched and delivered to a media platform.

## syndicated segment

A third-party marketplace audience sold by a seller through LiveRamp Data Marketplace, stored with `segment_type = 'Syndicated'`. Also called a 3P segment.

## 3P segment

Synonym for syndicated segment: third-party data packaged as a marketplace taxonomy node, distinct from the buyer's own first-party data.

## SSA

Syndicated segment activation workflow in Connect that authorizes a `field_id` / `value_id` pair for matching and delivery to a destination.

## digest

The packaged set of matched identifiers produced by a matching run, scoped to a destination account and delivery mode (FULL or INCREMENTAL).

## AIM mapping

Account Identifier Mapping that binds a Connect destination account to the platform's advertiser or seat identifiers so delivered audiences appear in the correct UI.

## cookie_reach

Estimated LiveRamp cookie-graph size for a segment (`ad_network_account_id = 2508` in `dms_segment_stats`). Not platform-matched keys and not impressions.

## ios_reach

Estimated iOS identifier-graph size for a segment (`ad_network_account_id = 6778`).

## android_reach

Estimated Android identifier-graph size for a segment (`ad_network_account_id = 21906`).

## cookie_overlap_percentage

Platform-level fraction used to estimate Connect cookie reach that is addressable on that platform: `cookie_reach * cookie_overlap_percentage`.

## FULL delivery

Delivery mode that sends the complete current matched identifier set to the destination (initial load or full refresh).

## INCREMENTAL delivery

Delivery mode that sends only net-new or dropped identifiers since the last successful digest.

## destination_account

Connect object tying a customer to a specific platform seat. Distribution metrics count distinct destination accounts where the segment is Enabled and the account is Active.

## field_id/value_id

The pair that uniquely identifies a syndicated taxonomy node in Connect delivery tables. Joins between marketplace segments and destination-account segments use this pair.

## deconfliction (AMC)

Measurement practice (including Amazon Marketing Cloud) that avoids double-counting overlapping third-party audiences when summing reach.

## Connect platform

LiveRamp Connect: configuration, matching, destination accounts, and delivery for activating marketplace data onto platforms.

## Data Marketplace

LiveRamp commercial catalog where syndicated segments are listed, entitled, and sold, independent of whether they are activated in Connect.

## distribution_rank

Dense rank of syndicated segments by active destination accounts, then active buyers, then `dms_segment_id`. Measures commercial footprint, not graph size.

## reach_rank

Dense rank of syndicated segments by cookie_reach, then ios_reach, then android_reach, then `dms_segment_id`. Measures identifier-graph size, not buyer count.
