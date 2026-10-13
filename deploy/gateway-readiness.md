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

## Shared web and direct FjordFlix streaming

The gateway now supports one opt-in direct FjordFlix media route alongside web
routes. Media DNS is unproxied, its hostname is excluded from the Origin Rule,
and Caddy only forwards `/media/*`. The existing verified HTTPS/nonce check is
shared with legacy streaming before activating a new stream URL. Store validation
allows only one FjordFlix stream writer across gateway and legacy profiles.
If FjordFlix cannot be contacted, other web routes can still synchronize.

Verification: 72 Python tests passed, both desktop/mobile browser suites passed,
and the isolated read-only/capability-restricted Caddy test passed with web and
media routes sharing a port. The latter checks a media Host header containing
the public port and rejects the media hostname's web root. After the final
web-sync isolation adjustment, all 33 gateway/stream tests passed again.
No active media DNS or production stream configuration was changed for this
feature. Real video playback remains a migration check in the setup guide.

## Per-domain readiness indicators

Gateway cards now show independent ready/pending/error indicators for each
hostname and only show Online when every route is ready. Web readiness uses a
verified public HTTPS request without redirects or a response-body download;
media readiness reuses the verified nonce/activation check. New DNS/TLS/connection
failures are pending for five minutes; failures of previously ready routes and
configuration errors are red. Cache results are discarded after profile edits,
VPN endpoint changes, unavailable relays or 90 seconds without a fresh check.

83 Python tests passed, including independent web/media readiness, recovery,
TLS/request behavior, error classification and stale-result rejection. The
gateway browser suite passed at desktop/mobile widths with all three colors,
accessible explanations and persisted settings. Existing streaming and legacy
profile tests remain passing. No production profile or DNS was changed.

## Automatic cleanup of removed gateway targets

Active A-records are marked with a gateway comment and their record IDs,
zone, IP and proxy mode are registered in a local `gateway-dns.json` ledger.
Removing a target and saving queues cleanup durably. The next enabled automatic
sync removes only unchanged owned records and updates/deletes only the gateway's
own Origin Rule. Re-added targets and hostnames handed to another profile cancel
their DNS deletion. Unknown legacy records and manually changed records are
preserved with a visible explanation. Stopping a gateway alone still retains DNS.

Cleanup tests cover deletion scope, unrelated-rule preservation, changed IP,
proxy mode, comment, ID and record type, failed-request/restart recovery,
re-adding, profile handoff, direct-media-only Origin Rule removal and replacement
credentials in the same zone. All 95 Python tests and the gateway desktop/mobile
browser suite passed. No production test domain was deleted during development.

## Cloudflare-only web access (live deployment)

Every web hostname now has a source-IP gate using Caddy's `remote_ip` matcher
and Cloudflare's published networks. It never authorizes forwarding headers.
Direct media retains its `/media/*` route on the shared port. The isolated
read-only/capability-restricted Caddy test verifies allowed and denied peers,
forged headers, mismatched SNI/Host, media connectivity and root rejection.
All 95 Python tests passed with the guarded configuration.

The change was deployed to the live manager and gateway observer using a derived
image of the existing release, changing only gateway.py. The old manager was
terminated without its normal VPN shutdown hook; Docker settings, mounts,
labels, network aliases, health check and restart policy were preserved. VPN
container start times remained unchanged. The replacement manager and observer
use the same new image, so observer recreation retains enforcement. Stopped
replacement containers were removed after verification; the original manager
configuration is backed up in the host's protected temporary deployment folder.

Live checks: HTTPS via Cloudflare returned 200; direct web access returned 403,
including forged forwarding headers and SNI/Host mixing; the direct media
connection check returned 200 and media root returned 404. Actual authenticated
video playback remains the user's normal application check. Reverting to the
old image would remove this protection on observer recreation.


## Minecraft Java sharing the gateway (2026-10-13)

One opt-in Java TCP target shares the HTTPS VPN port via caddy-l4 0.1.2.
A separate zone-scoped DNS Edit/Zone Read key supports a Minecraft domain in
another zone. DNS-only A and SRV records follow the current endpoint; owned
unchanged records are removed when Minecraft is disabled. Website records and
web Origin Rules are preserved. No Bedrock/UDP or PROXY protocol is provided.

106 Python tests passed, plus the desktop/mobile gateway browser suite and
an isolated read-only/capability-restricted real Caddy test sharing one port
between HTTPS and Java. TLS recognition checks record version bytes too:
a Java handshake for mc.nordprison.dk starts with 0x16 just like TLS, and the
container regression test uses that exact hostname. Source-IP web guards,
forged-header rejection, media restrictions and unknown hosts remain tested.

Live Java status at mc.nordprison.dk returned Paper 1.16.5/protocol 754 through
the gateway, while film and media returned HTTP 200 and direct web access 403.
The old Minecraft VPN was left running with its competing DNS updater disabled.
Manager and observer used the same corrected image; existing VPN containers
were not restarted during this deployment. Player login/gameplay remains a
manual check. A subsequent user update from the previous GitHub revision
replaced the live image; saved Minecraft settings remained intact. This commit
makes the tested implementation available to normal FjordHub updates.

## Stopped profile deletion and gameserver wording

The gameserver section now names its current supported type, Minecraft Java,
and uses an activation label rather than implying a choice to bypass VPN.
Other games/UDP are not advertised as supported by Java-specific DNS and probes.

Stopped profiles can be deleted with authenticated CSRF-protected POST and
explicit confirmation. Both container owners and states are checked before
removal; local data is removed only from the validated profile directory.
Other profiles, shared keys and Cloudflare DNS/rules are preserved. This makes
deleting a retired VPN independent of a hostname migration to a new gateway.
112 Python tests passed, including deletion scope, authorization, failure
handling and container ownership checks. Desktop/mobile gateway browser checks
passed including cancel, confirm, running-profile protection and persistence.

## FjordSeed sharing with a separate open torrent port

The secret-free seedbox export now includes a gateway capability boolean while
retaining relay_enabled=true for old clients. Updated FjordSeed leases a second
direct NAT-PMP TCP/UDP mapping; HTTPS/Minecraft retain the original mapping.
The Proton WireGuard profile and Caddy configuration are unchanged. Native
UID-1000 qBittorrent tests passed for TCP, encrypted TCP, uTP upload, download
from a different public VPN exit, supervisor failure closure and recovery.
Detailed verification and rollout: [FjordSeed release checks](https://github.com/qlerup/fjordseed/blob/main/deploy/shared-gateway-readiness.md).
