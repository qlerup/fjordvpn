# Deployment review

No blocking findings for a LAN-only, empty installation. User requested that
existing VPNs remain entirely independent; no migration or legacy SSH integration
is present. No live VPN tunnel is started during verification.

Checked: strict upload parser, auth/CSRF/Host guards, duplicate-key protection,
atomic persistence, labelled Docker ownership, independent namespaces, persisted
power state, stale-status handling, responsive UI and isolated browser flows.

Deployment checklist: install pinned Python requirements; build relay image;
pull pinned Gluetun; provision dedicated app user and private state directory;
enable systemd service; verify LAN binding and generated login; check empty
profiles; reboot new LXC and verify service readiness and unchanged old VPNs.

Important follow-up: test a real, new Proton profile when the user is ready;
reserve the container's DHCP address if a stable UI URL is desired; protect
backups of the private data directory. TLS for administration would be required
before exposure beyond the trusted LAN. Current scope is LAN-only.

Nice to have later: UDP relay, integrated DNS updates, credential replacement,
password-change UI and explicit per-profile delete/export workflows.

Rollback: stop LXC1014; no other VPN depends on it. Preserve /var/lib/fjordvpn
before updating app code. Stopping the UI alone does not stop running tunnels.
