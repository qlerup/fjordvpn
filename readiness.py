"""One definition of fully operational for the dashboard and notifications."""
import ipaddress


def readiness(row):
    waiting = []
    if not row.get('desired'):
        return dict(ready=False, waiting=['VPN er slukket.'])
    if row.get('state') != 'online':
        waiting.append('VPN-forbindelse')
    try:
        public = ipaddress.IPv4Address(row.get('public_ip', '')).is_global
    except ipaddress.AddressValueError:
        public = False
    if not public:
        waiting.append('Offentlig IP')
    port = row.get('public_port')
    if type(port) is not int or not 1 <= port <= 65535:
        waiting.append('Offentlig port')
    gateway = row.get('gateway', {})
    if (row.get('relay_enabled') or gateway.get('enabled')) and not row.get('relay_active'):
        waiting.append('Viderestilling')
    if row.get('relay_enabled') and not gateway.get('enabled') and not row.get('target_ready'):
        waiting.append('Kontakt til den lokale tjeneste')
    if row.get('stream_sync', {}).get('enabled') and not row.get('stream_status', {}).get('ok'):
        waiting.append('DNS og streamadresse')
    if row.get('internet_gateway', {}).get('enabled') and row.get('internet_status', {}).get('state') != 'ready':
        waiting.append('Serverens VPN-forbindelse')
    if gateway.get('enabled'):
        if row.get('gateway_status', {}).get('state') != 'ready':
            waiting.append('Gatewayens DNS og HTTPS')
        from relay.gateway_tcp import target_rows
        for target in target_rows(gateway.get('tcp', {})):
            if row.get('tcp_status', {}).get(target['id'], {}).get('state') != 'ready':
                waiting.append('TCP: ' + target['name'])
    from configuration import plex_targets
    for target in plex_targets(row):
        if target.get('plex_sync') and row.get('plex_status', {}).get(target['id'], {}).get('state') != 'ready':
            waiting.append('Plex-fjernadgang')
    return dict(ready=not waiting, waiting=waiting)
