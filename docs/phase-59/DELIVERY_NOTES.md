# Delivery Notes

- Branch: `phase/59-current-government-and-public-domain`
- Intended review tag: `phase-59-review`
- Rollback: deploy the preceding `phase-58-review` tag. The SQLite schema
  addition is backward compatible and does not require deleting the database
  volume.
- Public domain status at delivery: `cupdky.cn` and `jobs.cupdky.cn` both
  return `NXDOMAIN`. Caddy and HTTPS automation cannot create a DNS zone or
  domain registration. Activate the registered domain's DNS service, then add
  `A  jobs  81.70.62.174` with TTL `600` or `Auto`.
- This release does not claim the requested 75/100 completion threshold. It
  improves correctness and daily operation; nationwide current provincial
  public-institution/civil-service coverage remains the main product gap.
