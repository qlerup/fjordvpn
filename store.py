import json
import os
from pathlib import Path
import re
import threading
import uuid

from configuration import parse_wireguard, validate_target
from stream_sync import settings as stream_settings


def atomic(path, data, mode=0o600):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class Store:
    def __init__(self, root, networks):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        (self.root / 'profiles').mkdir(exist_ok=True, mode=0o700)
        self.networks = networks
        self.lock = threading.RLock()
        (self.root / 'cloudflare-keys').mkdir(exist_ok=True, mode=0o700)
        self.migrate_keys()

    def key_path(self, ident):
        if not isinstance(ident, str) or not re.fullmatch('[0-9a-f]{32}', ident):
            raise ValueError('Vælg en gemt Cloudflare-nøgle.')
        return self.root / 'cloudflare-keys' / (ident + '.json')

    def key(self, ident):
        try:
            return json.loads(self.key_path(ident).read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise ValueError('Cloudflare-nøglen findes ikke.') from None

    def keys(self):
        with self.lock:
            values = [json.loads(p.read_text(encoding='utf-8')) for p in (self.root/'cloudflare-keys').glob('*.json')]
            return [{k:v for k,v in item.items() if k != 'token'} for item in sorted(values, key=lambda v:v['name'].lower())]

    def save_key(self, data, ident=None):
        with self.lock:
            old = self.key(ident) if ident else {}
            name = str(data.get('name', '')).strip()
            zone = str(data.get('zone_id', '')).strip()
            token = data.get('token') or old.get('token', '')
            if not name or len(name)>64:
                raise ValueError('Giv nøglen et navn på højst 64 tegn.')
            if not re.fullmatch('[0-9a-f]{32}', zone):
                raise ValueError('Indtast et gyldigt Cloudflare Zone ID.')
            if not isinstance(token, str) or not re.fullmatch('[A-Za-z0-9_-]{20,256}', token):
                raise ValueError('Indtast et gyldigt Cloudflare API-token.')
            if old and old['zone_id'] != zone and any(p.get('stream_sync', {}).get('key_id') == ident for p in self.all()):
                raise ValueError('Nøglen bruges af en VPN. Opret en ny nøgle for en anden zone.')
            ident = ident or uuid.uuid4().hex
            atomic(self.key_path(ident), json.dumps(dict(id=ident, name=name, zone_id=zone, token=token)))
            return ident

    def migrate_keys(self):
        for profile in self.all():
            config = profile.get('stream_sync', {})
            path = self.directory(profile['id']) / 'cloudflare-token'
            if path.exists() and not config.get('key_id') and config.get('zone_id'):
                ident = profile['id']
                atomic(self.key_path(ident), json.dumps(dict(id=ident, name=profile['name'],
                    zone_id=config['zone_id'], token=path.read_text().strip())))
                config['key_id'] = ident
                self.save(profile)

    def sync_token(self, profile):
        ident = profile.get('stream_sync', {}).get('key_id')
        if ident:
            return self.key(ident)['token']
        return (self.directory(profile['id'])/'cloudflare-token').read_text().strip()

    def directory(self, ident):
        if not re.fullmatch('[0-9a-f]{32}', ident):
            raise ValueError('Ukendt forbindelse.')
        return self.root / 'profiles' / ident

    def all(self):
        with self.lock:
            return [json.loads(p.read_text(encoding='utf-8')) for p in sorted((self.root / 'profiles').glob('*/profile.json'))]

    def get(self, ident):
        try:
            return json.loads((self.directory(ident) / 'profile.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise ValueError('Forbindelsen findes ikke.') from None

    def save(self, profile):
        folder = self.directory(profile['id'])
        atomic(folder / 'profile.json', json.dumps(profile))
        # Mount the directory, not an individual file, so atomic edits are visible.
        atomic(folder / 'relay' / 'settings.json', json.dumps({
            'host': profile['host'], 'port': profile['port'],
            'enabled': profile['relay_enabled'], 'revision': profile['revision']}))

    def prepare_stream(self, data, previous=None):
        previous = previous or {}
        data = dict(data)
        supplied = data.get('stream_sync', previous.get('stream_sync', {}))
        key_id = supplied.get('key_id') if isinstance(supplied, dict) else None
        if isinstance(supplied, dict) and supplied.get('enabled') and 'key_id' in supplied and not key_id:
            raise ValueError('Vælg en gemt nøgle fra Cloudflare-nøgler.')
        if key_id:
            key = self.key(key_id)
            data['stream_sync'] = dict(supplied, zone_id=key['zone_id'])
        token = data.get('cloudflare_token', '')
        if not isinstance(token, str) or (token and not re.fullmatch(r'[A-Za-z0-9_-]{20,256}', token)):
            raise ValueError('Ugyldigt Cloudflare API-token.')
        saved = bool(previous.get('id') and (self.directory(previous['id']) / 'cloudflare-token').exists())
        config = stream_settings(data, previous.get('stream_sync', {}), bool(key_id) or bool(token) or saved)
        if key_id:
            config = dict(config, key_id=key_id)
        if config.get('enabled'):
            if config.get('fjordflix', True) and not data.get('relay_enabled'):
                raise ValueError('TCP-videresendelse skal være aktiv for automatisk streaming.')
            for other in self.all():
                if other['id'] != previous.get('id') and other.get('stream_sync', {}).get('enabled'):
                    other_config = other['stream_sync']
                    if other_config['domain'] == config['domain']:
                        raise ValueError('Domænet styres allerede af en anden VPN. Vælg et andet domæne.')
                    if config.get('fjordflix', True) and other_config.get('fjordflix', True):
                        raise ValueError('Kun én VPN kan styre FjordFlix-streamadressen. Slå FjordFlix-valget fra på den anden først.')
        return config, token

    def create(self, data, raw, reserved=()):
        with self.lock:
            target = validate_target(data, self.networks)
            stream, token = self.prepare_stream(data)
            config, metadata = parse_wireguard(raw)
            if metadata['fingerprint'] in set(reserved) | {p['fingerprint'] for p in self.all()}:
                raise ValueError('Denne VPN-nøgle bruges allerede. Opret en ny konfiguration hos Proton, så den eksisterende VPN ikke bliver afbrudt.')
            if len(self.all()) >= 20:
                raise ValueError('Der kan højst oprettes 20 forbindelser.')
            profile = dict(target, **metadata, id=uuid.uuid4().hex, desired=False, revision=uuid.uuid4().hex)
            profile['stream_sync'] = stream
            folder = self.directory(profile['id'])
            folder.mkdir(mode=0o700)
            for name in ('state', 'relay', 'observed'):
                (folder / name).mkdir(mode=0o700)
            atomic(folder / 'wg0.conf', config)
            if token:
                atomic(folder / 'cloudflare-token', token)
            self.save(profile)
            return profile

    def edit(self, ident, data):
        with self.lock:
            p = self.get(ident)
            stream, token = self.prepare_stream(data, p)
            p.update(validate_target(data, self.networks), revision=uuid.uuid4().hex)
            p['stream_sync'] = stream
            if token:
                atomic(self.directory(ident) / 'cloudflare-token', token)
            self.save(p)
            return p

    def power(self, ident, wanted):
        if type(wanted) is not bool:
            raise ValueError('Ugyldigt valg.')
        with self.lock:
            p = self.get(ident)
            p['desired'] = wanted
            self.save(p)
            return p
