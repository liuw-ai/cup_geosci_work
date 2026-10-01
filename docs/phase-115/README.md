# Phase 115: Sichuan official recruitment cross-domain adapter

## Purpose

The Sichuan geology bureau publishes the announcement on `dkj.sc.gov.cn`,
but its official position-table links are served by the Sichuan Personnel
Examination Network at `www.scpta.com.cn`. The previous source contract only
allowed the bureau host, so a valid official recruitment detail page could be
discarded before its attachment was processed.

## Change

`data/provincial_sources.json` now records both official hosts for
`sichuan-geology-bureau` and adds:

- the bureau's official search result endpoint for recruitment notices;
- the verified Sichuan Personnel Examination Network detail URL pattern;
- the verified 2026 first-half Sichuan geology bureau announcement as a
  regression input;
- the second host in the attachment allowlist.

This is a source-chain fix, not a publication bypass. The registered 2026
announcement says online registration ended on 2026-03-24, so its rows remain
closed and cannot enter the current student-facing corpus. The URL is kept to
ensure that a future refresh can parse the official attachment and then apply
the normal major, degree, location, headcount and deadline gates.

## Verification

- Server direct probe: the bureau notice column returned HTTP 200.
- The official search result linked the announcement to
  `https://www.scpta.com.cn/front/News/info/e8bc770cc1184debaede1117b6e6a294`.
- The detail page exposed three official attachments, including the position
  and conditions table.
- Registry validation loaded 80 combined national/provincial sources and
  accepted both allowlisted hosts.
- No current job was activated in this phase; expired March positions were
  deliberately excluded.

## Acceptance

This phase improves reproducible future coverage but does not by itself meet
the 75-point gate. The next quantitative gate remains activation of the
China Earthquake Administration batch after its 2026-10-10 opening-day
recheck, or an independently verified current Three-Barrel-Oil subsidiary
detail chain.
