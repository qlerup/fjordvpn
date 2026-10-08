"""Locally managed Cloudflare ingress, independent of Proton namespaces."""
import base64
import json
import os
import re
import uuid

import docker

from configuration import validate_target
from store import atomic

CLOUDFLARED = 'cloudflare/cloudflared:2026.9.3'
REVISION = 'dk.fjordvpn.gateway-revision'


def hostname(value):
    if not isinstance(value, str):
        raise ValueError('Indtast et fuldt domænenavn.')
    value = value.strip().lower().rstrip('.')
    labels = value.split('.')
    if (len(value) > 253 or len(labels) < 2 or labels[-1].isdigit()
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', s) for s in labels)):
        raise ValueError('Indtast et fuldt domænenavn uden sti, port eller wildcard.')
    return value


class GatewayStore:
    def __init__(self, store):
        self.store = store
        self.path = store.root / 'gateway.json'

    def get(self):
        with self.store.lock:
            if self.path.exists():
                return json.loads(self.path.read_text(encoding='utf-8'))
            return {'id': uuid.uuid4().hex, 'revision': '', 'enabled': False,
                    'routes': [], 'credentials': None}

    def public(self):
        data = self.get()
        credentials = data.pop('credentials')
        tunnel_id = credentials['TunnelID'] if credentials else ''
        return dict(data, configured=bool(credentials), tunnel_id=tunnel_id,
                    dns_target=f'{tunnel_id}.cfargotunnel.com' if tunnel_id else '')

    def save(self, data):
        if not isinstance(data, dict):
            raise ValueError('Ugyldige indstillinger.')
        with self.store.lock:
            current = self.get()
            if data.get('revision') != current['revision']:
                raise ValueError('Indstillingerne er ændret. Genindlæs siden før du gemmer.')
            enabled = data.get('enabled', False)
            routes = data.get('routes')
            if type(enabled) is not bool or not isinstance(routes, list) or len(routes) > 100:
                raise ValueError('Vælg højst 100 domæneregler og en gyldig tunneltilstand.')
            validated, seen = [], set()
            for route in routes:
                if not isinstance(route, dict):
                    raise ValueError('Ugyldig domæneregel.')
                domain = hostname(route.get('hostname'))
                if domain in seen:
                    raise ValueError('Hvert subdomæne må kun optræde én gang.')
                seen.add(domain)
                target = validate_target(dict(name=domain[:64], host=route.get('host'),
                    port=route.get('port'), relay_enabled=True), self.store.networks)
                scheme = route.get('scheme', 'http')
                active = route.get('enabled', True)
                if scheme not in ('http', 'https') or type(active) is not bool:
                    raise ValueError('Vælg HTTP eller HTTPS og en gyldig regeltilstand.')
                server_name = route.get('origin_server_name', '')
                if not isinstance(server_name, str):
                    raise ValueError('Indtast et gyldigt navn til HTTPS-certifikatet.')
                if server_name:
                    server_name = hostname(server_name)
                validated.append(dict(hostname=domain, host=target['host'], port=target['port'],
                    scheme=scheme, enabled=active, origin_server_name=server_name))
            credentials = current['credentials']
            if data.get('credentials') is not None:
                try:
                    supplied = data['credentials']
                    tunnel_id = str(uuid.UUID(supplied['TunnelID']))
                    account = supplied['AccountTag']
                    secret = supplied['TunnelSecret']
                    if not re.fullmatch('[a-fA-F0-9]{32}', account) or len(base64.b64decode(secret, validate=True)) != 32:
                        raise ValueError()
                    credentials = dict(TunnelID=tunnel_id, AccountTag=account, TunnelSecret=secret)
                except (KeyError, TypeError, ValueError, AttributeError):
                    raise ValueError('Vælg tunnelens credentials JSON-fil fra cloudflared tunnel create.') from None
            if enabled and (not credentials or not any(r['enabled'] for r in validated)):
                raise ValueError('Indlæs tunnelens JSON-fil og tilføj mindst én aktiv regel før start.')
            current.update(enabled=enabled, routes=validated, credentials=credentials, revision=uuid.uuid4().hex)
            atomic(self.path, json.dumps(current))
            return self.public()

    def render(self, data):
        rules = []
        for route in data['routes']:
            if route['enabled']:
                rule = {'hostname': route['hostname'],
                        'service': f"{route['scheme']}://{route['host']}:{route['port']}"}
                if route['origin_server_name']:
                    rule['originRequest'] = {'originServerName': route['origin_server_name']}
                rules.append(rule)
        rules.append({'service': 'http_status:404'})
        return {'tunnel': data['credentials']['TunnelID'],
                'credentials-file': '/etc/cloudflared/credentials.json', 'ingress': rules}


class GatewayRuntime:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = GatewayStore(runtime.store)
        self.error = ''

    def reconcile(self):
        # Old installations without gateway configuration do not touch Docker.
        if not self.store.path.exists():
            return
        data = self.store.get()
        rt = self.runtime
        container = rt.container(data['id'], 'gateway')
        if not data['enabled']:
            if container:
                container.update(restart_policy={'Name': 'no'})
                if container.status != 'exited':
                    container.stop(timeout=10)
            return
        if container and container.labels.get(REVISION) != data['revision']:
            container.stop(timeout=10)
            container.remove()
            container = None
        if container is None:
            # Pull before creating; containers.create does not pull missing images.
            try:
                rt.client.images.get(CLOUDFLARED)
            except docker.errors.ImageNotFound:
                rt.client.images.pull(CLOUDFLARED)
            folder = rt.store.root / 'gateway'
            folder.mkdir(exist_ok=True, mode=0o700)
            atomic(folder / 'credentials.json', json.dumps(data['credentials']))
            # JSON is also valid YAML and avoids user-controlled config syntax.
            atomic(folder / 'config.yml', json.dumps(self.store.render(data)))
            labels = rt.labels(data['id'], 'gateway')
            labels[REVISION] = data['revision']
            container = rt.client.containers.create(CLOUDFLARED,
                name=f"fjordvpn-{data['id']}-gateway", labels=labels,
                command=['tunnel', '--config', '/etc/cloudflared/config.yml', '--no-autoupdate', 'run'],
                user=f'{getattr(os, "getuid", lambda: 0)()}:{getattr(os, "getgid", lambda: 0)()}',
                volumes={str(rt.host_root / 'gateway'): {'bind': '/etc/cloudflared', 'mode': 'ro'}},
                read_only=True, cap_drop=['ALL'], security_opt=['no-new-privileges:true'],
                restart_policy=rt.restart_policy, mem_limit='256m',
                log_config=docker.types.LogConfig(type='json-file', config={'max-size':'2m','max-file':'2'}))
        if container.status != 'running':
            container.update(restart_policy=rt.restart_policy)
            container.start()

    def tick(self):
        try:
            self.reconcile()
            self.error = ''
        except Exception:
            self.error = 'Cloudflare-containeren kunne ikke opdateres. Kontrollér Docker og billeddownload.'

    def status(self):
        data = self.store.public()
        data.update(state='off', message='Cloudflare Tunnel er slukket.')
        if self.error:
            data.update(state='error', message=self.error)
        elif self.store.path.exists():
            try:
                container = self.runtime.container(data['id'], 'gateway')
                if data['enabled']:
                    if container and container.status == 'running' and container.labels.get(REVISION) == data['revision']:
                        data.update(state='running', message='Containeren kører. DNS og adgang til dine tjenester skal også være opsat; ekstern adgang er ikke verificeret.')
                    else:
                        data.update(state='connecting', message='Starter eller opdaterer Cloudflare Tunnel…')
                elif container and container.status == 'running':
                    data.update(state='stopping', message='Stopper Cloudflare Tunnel…')
            except Exception:
                data.update(state='unknown', message='Docker-status er ikke tilgængelig.')
        return data

    def shutdown(self):
        if self.store.path.exists():
            data = self.store.get()
            container = self.runtime.container(data['id'], 'gateway')
            if container:
                container.update(restart_policy={'Name': 'no'})
                container.stop(timeout=5)
