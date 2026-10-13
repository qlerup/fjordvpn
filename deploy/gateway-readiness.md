# VPN gateway release checks

The additive gateway feature is implemented beside legacy TCP and torrent
profiles. No production VPN profile or DNS record was changed during development.

## Verified

- 64 Python tests passed, including legacy profile preservation, domain ownership,
  secret-free API responses, Cloudflare rule isolation and stale-state rejection.
- Existing desktop/mobile browser suite passed.
- Gateway browser suite passed: creation, multiple targets, editing, persistence,
  default-off state and untouched legacy profile data.
- Docker image built successfully on the production Docker host under the
  separate test tag `fjordvpn-gateway-check:20261013`.
- Isolated container test with `--network none` passed: real Caddy/Cloudflare
  module validation, trusted TLS, two distinct hostname routes, preserved path
  and Host, forwarded HTTPS scheme and rejection of unconfigured hostnames.
- Production Seedbox and FjordFlix VPN containers were left running.

## Deployment requirements

- A fresh Proton WireGuard configuration with NAT-PMP; an already active key
  must not be reused.
- Explicit subdomains and private LAN targets in the selected Cloudflare zone.
- Zone-scoped DNS Edit, Zone Read and Origin Rules Edit permissions; existing
  DNS-only keys may not have the latter two permissions.
- Cloudflare Full (strict) mode and origin Host/proxy settings on local apps.
- Back up the entire data directory, including gateway certificate storage.
- The current manager's shutdown handler stops its managed VPNs. This release
  was not deployed over that manager because the user required uninterrupted
  existing connections. Schedule a manager update separately.

## Remaining validation

No new user-provided VPN configuration or domain mapping was activated. Public
DNS synchronization, ACME issuance and application login/streaming through real
selected hostnames therefore still require an opt-in configured integration test.

## Rollback

Stop a new gateway independently of old profiles. Keep its data for rollback.
Cloudflare records and rules are not automatically deleted on stop; review only
the gateway's own `fjordvpn_gateway_<profile-id>` rule and its selected A-records.
Do not roll back the manager while a new gateway is expected to remain active;
older observers cannot serve the gateway mode. Legacy profile schemas were not
migrated, and their original relay paths remain compatible.

## Live gateway follow-up (2026-10-13)

The user subsequently activated a separate gateway for two test hostnames.
Its observer initially failed to execute Caddy with EPERM: the executable's
`security.capability` attribute conflicted with the observer's dropped capabilities.
The Dockerfile now removes that attribute; high forwarded ports need no bind
capability. A derived image with the same fix passed the routing/TLS container
test with a read-only root filesystem, `--cap-drop ALL`,
`--security-opt no-new-privileges:true` and `--network none`.

Only the new gateway observer was replaced with that image. Existing Seedbox
and FjordFlix VPN containers remained running. The gateway became online, but
Cloudflare synchronization remains blocked: zone and DNS reads succeeded,
while the Origin Rules entrypoint returned HTTP 403 (code 10000). A zone-scoped
token with Origin Rules Edit is required before public DNS/HTTPS verification.
The manager must be updated to this Dockerfile before recreating the gateway
observer, so subsequent starts also use the permanent capability fix.
