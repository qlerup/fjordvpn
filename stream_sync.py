"""Opt-in Cloudflare DNS and verified FjordFlix endpoint synchronization."""
import ipaddress
import json
import re
import secrets
import time
from urllib.parse import urlsplit

import requests


def settings(data, previous, token_exists):
    if 'stream_sync' not in data:
        return previous
    value = data['stream_sync']
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise ValueError('Ugyldige streamindstillinger.')
    domain = str(value.get('domain', '')).strip().lower()
    zone = str(value.get('zone_id', '')).strip()
    flix = value.get('fjordflix', previous.get('fjordflix', bool(previous.get('enabled'))))
    if type(flix) is not bool:
        raise ValueError('Ugyldigt valg af FjordFlix-synkronisering.')
    if value['enabled']:
        if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain) or len(domain) > 253:
            raise ValueError('Indtast videodomænet uden https, port eller sti.')
        if not re.fullmatch(r'[0-9a-f]{32}', zone):
            raise ValueError('Indtast Cloudflare Zone ID (32 tegn).')
        if not token_exists:
            raise ValueError('Tilføj et Cloudflare API-token med DNS Edit for zonen.')
        if flix and not data.get('relay_enabled'):
            raise ValueError('Aktivér TCP-videresendelse til HTTPS-gatewayen først.')
    return dict(enabled=value['enabled'], domain=domain, zone_id=zone, fjordflix=flix)


class StreamSync:
    def __init__(self, store, runtime):
        self.store, self.runtime = store, runtime
        self.statuses, self.next_check = {}, {}

    def cf(self, token, method, path, **kwargs):
        with requests.Session() as session:
            session.trust_env = False
            response = session.request(method, 'https://api.cloudflare.com/client/v4' + path,
                headers={'Authorization': 'Bearer ' + token}, timeout=10, allow_redirects=False, **kwargs)
        if not response.ok:
            raise ValueError('Cloudflare afviste kaldet. Kontrollér token, Zone ID og DNS-rettigheder.')
        body = response.json()
        if body.get('success') is not True:
            raise ValueError('Cloudflare kunne ikke opdatere DNS.')
        return body['result']

    def dns(self, config, token, address):
        path = '/zones/' + config['zone_id'] + '/dns_records'
        records = self.cf(token, 'GET', path, params={'name': config['domain'], 'per_page': 100})
        # Never delete competing records or silently replace CNAME/IPv6 routing.
        if len(records) != 1 or records[0].get('type') != 'A':
            raise ValueError('Videodomænet skal have præcis én A-record og ingen CNAME/AAAA-records i Cloudflare.')
        record = records[0]
        if record.get('name', '').lower() != config['domain']:
            raise ValueError('Cloudflare returnerede et andet domæne.')
        if record.get('content') != address or record.get('proxied'):
            self.cf(token, 'PATCH', path + '/' + record['id'],
                    json={'content': address, 'proxied': False, 'ttl': 60})

    def flix(self, action, *values):
        container = self.runtime.client.containers.get('fjordflix')
        code = '''import os, sqlite3, json
from app import media
def db():
 c=sqlite3.connect(os.path.join(os.environ.get('DATA_DIR','/data'),'fjordflix.db'),timeout=15)
 c.row_factory=sqlite3.Row
 return c
media.init(db)
'''
        if action == 'probe':
            code += f'media.prepare_probe({values[0]!r},{values[1]!r},db)\n'
        elif action == 'activate':
            code += f'media.save_config({values[0]!r},{values[1]!r},db)\n'
        code += "print(json.dumps(dict(zip(('media','web'),media.config()))))"
        result = container.exec_run(['python', '-c', code])
        if result.exit_code:
            raise ValueError('FjordFlix kunne ikke kontaktes. Kontrollér at den er opdateret og kører.')
        return json.loads(result.output.decode().strip().splitlines()[-1])

    def sync(self, profile, observed):
        config = profile['stream_sync']
        address = str(ipaddress.IPv4Address(observed['public_ip']))
        if not ipaddress.ip_address(address).is_global:
            raise ValueError('VPN har endnu ingen offentlig IPv4-adresse.')
        token = self.store.sync_token(profile)
        if not config.get('fjordflix', True):
            self.dns(config, token, address)
            return config['domain']
        port = int(observed['public_port'])
        if not 1 <= port <= 65535:
            raise ValueError('VPN har endnu ingen offentlig port.')
        current = self.flix('status')
        host = config['domain'] + (f':{port}' if port != 443 else '')
        url = 'https://' + host
        # The initial guide establishes certificate/web origin; don't replace another domain.
        if urlsplit(current['media']).hostname != config['domain'] or not current['web'].startswith('https://'):
            raise ValueError('Opsæt dette videodomæne og webadressen i FjordHubs streamguide først.')
        self.dns(config, token, address)
        # Verify IP-only changes too, with the normal TLS certificate checks.
        nonce = secrets.token_urlsafe(32)
        self.flix('probe', host, nonce)
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(url + '/media/connection-check', timeout=10, allow_redirects=False)
        if response.status_code != 200 or response.json().get('nonce') != nonce:
            raise ValueError('DNS er opdateret; afventer korrekt HTTPS-forbindelse til FjordFlix.')
        # Configuration or the VPN endpoint may have changed while DNS/HTTPS was checked.
        with self.store.lock:
            fresh = self.store.get(profile['id'])
            latest = self.runtime.status(fresh)
            if (fresh != profile or latest['state'] != 'online' or not latest['relay_active']
                    or latest['public_ip'] != address or int(latest['public_port']) != port):
                raise ValueError('VPN-indstillingerne blev ændret. Prøver igen med den nye adresse.')
            if current['media'] != url:
                self.flix('activate', url, current['web'])
        return url

    def tick(self):
        for profile in self.store.all():
            ident = profile['id']
            if not profile.get('stream_sync', {}).get('enabled'):
                self.statuses.pop(ident, None)
                self.next_check.pop(ident, None)
                continue
            if time.monotonic() < self.next_check.get(ident, 0):
                continue
            self.next_check[ident] = time.monotonic() + 30
            try:
                observed = self.runtime.status(profile)
                flix = profile['stream_sync'].get('fjordflix', True)
                if observed['state'] != 'online' or (flix and not observed['relay_active']):
                    self.statuses[ident] = {'ok': False, 'message': 'Afventer VPN og aktiv TCP-videresendelse.' if flix else 'Afventer VPN.'}
                    continue
                url = self.sync(profile, observed)
                self.statuses[ident] = {'ok': True, 'message': 'DNS og streamadresse er ajour.' if flix else 'Cloudflare DNS er ajour.', 'url': url}
            except ValueError as exc:
                # Only our validation messages are safe to display; parser errors are generic.
                message = str(exc) if not isinstance(exc, json.JSONDecodeError) else 'Ugyldigt svar fra forbindelsestesten.'
                self.statuses[ident] = {'ok': False, 'message': message}
            except Exception:
                self.statuses[ident] = {'ok': False, 'message': 'Synkronisering afventer forbindelsen. Prøver igen automatisk.'}
