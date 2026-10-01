import json
import os
from pathlib import Path
import re
import threading
import uuid

from configuration import parse_wireguard, validate_target


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

    def create(self, data, raw, reserved=()):
        with self.lock:
            target = validate_target(data, self.networks)
            config, metadata = parse_wireguard(raw)
            if metadata['fingerprint'] in set(reserved) | {p['fingerprint'] for p in self.all()}:
                raise ValueError('Denne VPN-nøgle bruges allerede. Opret en ny konfiguration hos Proton, så den eksisterende VPN ikke bliver afbrudt.')
            if len(self.all()) >= 20:
                raise ValueError('Der kan højst oprettes 20 forbindelser.')
            profile = dict(target, **metadata, id=uuid.uuid4().hex, desired=False, revision=uuid.uuid4().hex)
            folder = self.directory(profile['id'])
            folder.mkdir(mode=0o700)
            for name in ('state', 'relay', 'observed'):
                (folder / name).mkdir(mode=0o700)
            atomic(folder / 'wg0.conf', config)
            self.save(profile)
            return profile

    def edit(self, ident, data):
        with self.lock:
            p = self.get(ident)
            p.update(validate_target(data, self.networks), revision=uuid.uuid4().hex)
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
