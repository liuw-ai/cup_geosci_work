# Public Access Diagnostic: 2026-10-24

## Scope

This record covers the public access failure reported from a mobile cellular
network for the R4 release. It contains no credentials, account identifiers,
or client addresses.

## Confirmed facts

- `cupgeojob.com` resolves to the production Lighthouse public address.
- The R4 Web, Worker, and Caddy containers are healthy.
- Caddy listens on public TCP ports 80 and 443; the host firewall and the
  Lighthouse firewall both allow TCP 443 from all IPv4 addresses.
- The local Caddy TLS endpoint presents a valid Let's Encrypt certificate for
  `cupgeojob.com`; chain verification and TLS 1.3 negotiation succeed.
- A direct client connection reaches the instance and delivers a TLS
  ClientHello. The connection is then reset before the server can return its
  TLS response. The reset is not emitted by the Caddy container.
- An independent public TLS probe also could not complete a TLS exchange with
  the hostname.

## Interpretation

This is not an application-image, Caddyfile, certificate-chain, Docker port,
or Linux firewall defect. The remaining failure domain is the public ingress
path outside the instance. Because the service is hosted in mainland China,
the domain's ICP filing status must be verified before changing infrastructure.
Domain registration alone is not an ICP filing.

## Required operator action

The domain owner must complete or verify the ICP filing in the cloud account
that owns the Lighthouse instance. This requires identity and website-subject
information, so it cannot be performed by an automated deployment process.
After the filing becomes effective, retest HTTPS from independent mobile and
non-mobile networks. If the failure persists, collect the exact browser error
and escalate it to Tencent Cloud with this record, the domain, the public IP,
and the failed test time.

No firewall, DNS, Caddy, or application configuration was changed during this
diagnostic.
