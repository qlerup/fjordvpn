import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid

from configuration import parse_wireguard, validate_target
from stream_sync import settings as stream_settings
from gateway import settings as gateway_settings


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
            if old and old['zone_id'] != zone and any(p.get('stream_sync', {}).get('key_id') == ident or p.get('gateway',{}).get('key_id')==ident
                    or p.get('gateway',{}).get('minecraft',{}).get('key_id')==ident for p in self.all()):
                raise ValueError('Nøglen bruges af en VPN. Opret en ny nøgle for en anden zone.')
            ident = ident or uuid.uuid4().hex
            atomic(self.key_path(ident), json.dumps(dict(id=ident, name=name, zone_id=zone, token=token)))
            for profile in self.all():
                if profile.get('gateway',{}).get('key_id')==ident:
                    atomic(self.directory(profile['id'])/'relay/gateway-token',token)
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
        relay={
            'host': profile['host'], 'port': profile['port'],
            'enabled': profile['relay_enabled'], 'revision': profile['revision']}
        gateway=profile.get('gateway',{})
        if gateway.get('enabled'):
            relay.update(enabled=True,mode='gateway',routes=gateway['routes'],minecraft=gateway.get('minecraft',{'enabled':False}),
                         tcp=gateway.get('tcp',{'enabled':False}))
            atomic(folder/'relay/gateway-token',self.key(gateway['key_id'])['token'])
        atomic(folder / 'relay' / 'settings.json', json.dumps(relay))

    def gateway_dns(self, ident):
        path=self.directory(ident)/'gateway-dns.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'records':{},'pending':[],'warnings':[]}

    def save_gateway_dns(self, ident, value):
        atomic(self.directory(ident)/'gateway-dns.json',json.dumps(value))

    def prepare_gateway(self,data,previous=None):
        previous=previous or {}
        config=gateway_settings(data.get('gateway',previous.get('gateway',{'enabled':False})),self.networks)
        if not config['enabled']:
            if previous.get('gateway',{}).get('enabled'):
                raise ValueError('Sluk gatewayen med Sluk-knappen. Dens profiltype kan ikke ændres.')
            return config
        if previous and not previous.get('gateway',{}).get('enabled'):
            raise ValueError('Opret en ny VPN-gateway. Eksisterende profiler kan ikke ændres til gateway.')
        stream=data.get('stream_sync',{})
        if not isinstance(stream,dict):
            raise ValueError('Ugyldige streamindstillinger.')
        if data.get('relay_enabled') or stream.get('enabled'):
            raise ValueError('Gatewayen bruger sin egen HTTPS-fordeling. TCP-relay og streamsynkronisering skal være slået fra.')
        self.key(config['key_id'])
        domains={r['domain'] for r in config['routes']}
        mc=config.get('minecraft',{})
        if mc.get('enabled'):
            self.key(mc['key_id']);domains.add(mc['domain'])
        flix=any(r.get('fjordflix') for r in config['routes'])
        for other in self.all():
            if other['id']==previous.get('id'):
                continue
            used={r['domain'] for r in other.get('gateway',{}).get('routes',[])}
            other_mc=other.get('gateway',{}).get('minecraft',{})
            if other_mc.get('enabled'):
                used.add(other_mc['domain'])
            if other.get('stream_sync',{}).get('enabled'):
                used.add(other['stream_sync']['domain'])
            if domains & used:
                raise ValueError('Et subdomæne styres allerede af en anden VPN. Brug et nyt navn.')
            if flix and (any(r.get('fjordflix') for r in other.get('gateway',{}).get('routes',[]))
                         or (other.get('stream_sync',{}).get('enabled') and other['stream_sync'].get('fjordflix',True))):
                raise ValueError('Slå FjordFlix-synkronisering fra på den gamle VPN først. Kun én profil kan styre streamadressen.')
        return config

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
                other_mc=other.get('gateway',{}).get('minecraft',{})
                if other['id']!=previous.get('id') and other_mc.get('enabled') and other_mc['domain']==config['domain']:
                    raise ValueError('Domænet styres allerede af gatewayens Minecraft-forbindelse.')
                if (other['id']!=previous.get('id') and config.get('fjordflix',True)
                        and any(r.get('fjordflix') for r in other.get('gateway',{}).get('routes',[]))):
                    raise ValueError('FjordFlix-streamadressen styres allerede af en VPN-gateway.')
                if other['id']!=previous.get('id') and any(r['domain']==config['domain'] for r in other.get('gateway',{}).get('routes',[])):
                    raise ValueError('Domænet styres allerede af en VPN-gateway.')
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
            gateway=self.prepare_gateway(data)
            config, metadata = parse_wireguard(raw)
            if metadata['fingerprint'] in set(reserved) | {p['fingerprint'] for p in self.all()}:
                raise ValueError('Denne VPN-nøgle bruges allerede. Opret en ny konfiguration hos Proton, så den eksisterende VPN ikke bliver afbrudt.')
            if len(self.all()) >= 20:
                raise ValueError('Der kan højst oprettes 20 forbindelser.')
            profile = dict(target, **metadata, id=uuid.uuid4().hex, desired=False, revision=uuid.uuid4().hex)
            profile['stream_sync'] = stream
            if gateway['enabled']:
                profile['gateway']=gateway
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
            gateway=self.prepare_gateway(data,p)
            stream, token = self.prepare_stream(data, p)
            old_gateway=p.get('gateway',{})
            p.update(validate_target(data, self.networks), revision=uuid.uuid4().hex)
            p['stream_sync'] = stream
            if gateway['enabled']:
                p['gateway']=gateway
            if token:
                atomic(self.directory(ident) / 'cloudflare-token', token)
            self.save(p)
            if old_gateway.get('enabled'):
                state=self.gateway_dns(ident);state['warnings']=[]
                old_zone=self.key(old_gateway['key_id'])['zone_id']
                new_zone=self.key(gateway['key_id'])['zone_id']
                keep={r['domain'] for r in gateway['routes']} if old_zone==new_zone else set()
                for row in old_gateway['routes']:
                    if row['domain'] not in keep:
                        entry={'domain':row['domain'],'zone_id':old_zone,'key_id':old_gateway['key_id']}
                        if entry not in state['pending']:
                            state['pending'].append(entry)
                old_mc=old_gateway.get('minecraft',{});new_mc=gateway.get('minecraft',{})
                if old_mc.get('enabled'):
                    mc_zone=self.key(old_mc['key_id'])['zone_id']
                    if not (new_mc.get('enabled') and new_mc['domain']==old_mc['domain']
                            and self.key(new_mc['key_id'])['zone_id']==mc_zone):
                        for name in [old_mc['domain'],'_minecraft._tcp.'+old_mc['domain']]:
                            entry={'domain':name,'zone_id':mc_zone,'key_id':old_mc['key_id']}
                            if entry not in state['pending']:
                                state['pending'].append(entry)
                self.save_gateway_dns(ident,state)
            return p

    def power(self, ident, wanted):
        if type(wanted) is not bool:
            raise ValueError('Ugyldigt valg.')
        with self.lock:
            p = self.get(ident)
            p['desired'] = wanted
            self.save(p)
            return p

    def delete(self, ident):
        """Remove one stopped profile's local data; shared keys and DNS remain."""
        with self.lock:
            p = self.get(ident)
            if p['desired']:
                raise ValueError('Sluk VPN’en, før du sletter den.')
            folder = self.directory(ident)
            if folder.is_symlink() or folder.resolve().parent != (self.root/'profiles').resolve():
                raise ValueError('Profilens datamappe kunne ikke slettes sikkert.')
            shutil.rmtree(folder)
