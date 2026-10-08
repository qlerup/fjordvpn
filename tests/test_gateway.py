import base64
import ipaddress
import json
from unittest.mock import MagicMock

import docker
import pytest

from app import create_app
from gateway import GatewayStore, REVISION
from runtime import Runtime
from store import Store
from test_app import FakeRuntime, login, config


def credentials():
    return {'TunnelID':'eb5c8ee0-cf44-4b47-b054-e30eb32a864c',
            'AccountTag':'a'*32, 'TunnelSecret':base64.b64encode(b'x'*32).decode()}


def route(**changes):
    return dict({'hostname':'fjordlens.gleruphub.dk','host':'192.168.1.50','port':3000,
                 'scheme':'http','enabled':True,'origin_server_name':''}, **changes)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path, [ipaddress.ip_network('192.168.1.0/24')])


def save(gateway, **changes):
    data = dict(gateway.public(), credentials=credentials(), routes=[route()], enabled=True)
    data.update(changes)
    return gateway.save(data)


def test_gateway_auth_csrf_and_secret_redaction(tmp_path):
    app = create_app(tmp_path, testing=True, runtime_factory=FakeRuntime)
    c = app.test_client()
    assert c.get('/api/gateway').status_code == 401
    assert c.post('/api/gateway', json={}).status_code == 403
    c, headers = login(app)
    data = c.get('/api/gateway').json
    assert not data['configured']
    payload = dict(revision=data['revision'], credentials=credentials(), routes=[route()], enabled=False)
    response = c.post('/api/gateway', headers=headers, json=payload)
    assert response.status_code == 200
    assert response.json['dns_target'].endswith('.cfargotunnel.com')
    assert credentials()['TunnelSecret'] not in response.text
    assert 'credentials' not in c.get('/api/gateway').json
    assert c.post('/api/gateway', headers=headers, json=payload).status_code == 400
    assert c.post('/api/gateway', headers=headers, json=[]).status_code == 400
    assert c.post('/api/gateway', headers=headers, json=dict(response.json, credentials={'TunnelSecret':'secret'})).status_code == 400


def test_routes_persist_and_keep_vpn_profiles_unchanged(store):
    profile = store.create({'name':'Proton'}, config())
    before = (store.directory(profile['id'])/'profile.json').read_bytes()
    gateway = GatewayStore(store)
    save(gateway, routes=[route(hostname='FjordLens.GlerupHub.dk.'), route(hostname='fjordbudget.gleruphub.dk', host='192.168.1.60', port=8080)])
    fresh = GatewayStore(Store(store.root, store.networks))
    data = fresh.get()
    assert data['routes'][0]['hostname'] == 'fjordlens.gleruphub.dk'
    ingress = fresh.render(data)['ingress']
    assert [r['service'] for r in ingress] == ['http://192.168.1.50:3000','http://192.168.1.60:8080','http_status:404']
    assert (store.directory(profile['id'])/'profile.json').read_bytes() == before
    save(fresh, credentials=None, routes=[route(enabled=False)], enabled=False)
    assert fresh.get()['credentials'] == credentials()
    assert fresh.render(fresh.get())['ingress'] == [{'service':'http_status:404'}]


@pytest.mark.parametrize('changes', [
    {'host':'8.8.8.8'}, {'host':'127.0.0.1'}, {'host':'169.254.169.254'},
    {'host':'192.168.1.255'}, {'port':True}, {'port':0}, {'port':65536},
    {'hostname':'*.gleruphub.dk'}, {'hostname':'a.test/path'}, {'hostname':'a.test\nservice: x'},
    {'hostname':'a.test:443'}, {'hostname':'192.168.1.50'}, {'scheme':'tcp'},
    {'origin_server_name':True}, {'enabled':'true'},
])
def test_invalid_routes_never_change_saved_state(store, changes):
    gateway = GatewayStore(store)
    save(gateway)
    before = gateway.path.read_bytes()
    with pytest.raises(ValueError):
        save(gateway, routes=[route(**changes)])
    assert gateway.path.read_bytes() == before


def test_duplicate_missing_credentials_and_tls(store):
    gateway = GatewayStore(store)
    with pytest.raises(ValueError): save(gateway, routes=[route(), route()])
    with pytest.raises(ValueError): save(gateway, credentials=None)
    with pytest.raises(ValueError): save(gateway, routes=[])
    save(gateway, routes=[route(scheme='https', port=443, origin_server_name='internal.gleruphub.dk')])
    rule = gateway.render(gateway.get())['ingress'][0]
    assert rule['originRequest'] == {'originServerName':'internal.gleruphub.dk'}
    assert 'noTLSVerify' not in json.dumps(rule)


def test_runtime_creation_reload_stop_and_no_published_ports(store):
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound('missing')
    container = MagicMock(status='created')
    client.containers.create.return_value = container
    rt = Runtime(store, client)
    gateway = rt.gateway.store
    saved = save(gateway)
    rt.gateway.reconcile()
    kwargs = client.containers.create.call_args.kwargs
    assert 'ports' not in kwargs and 'network_mode' not in kwargs
    assert kwargs['cap_drop'] == ['ALL'] and kwargs['read_only']
    assert 'credentials' not in json.dumps(kwargs['labels'])
    assert (store.root/'gateway/config.yml').exists()
    container.start.assert_called_once()
    container.labels = kwargs['labels']; container.status = 'running'
    client.containers.get.side_effect = None; client.containers.get.return_value = container
    rt.gateway.reconcile()
    container.remove.assert_not_called()
    assert rt.gateway.status()['state'] == 'running'
    # Persisting a new revision replaces only the owned gateway.
    save(gateway, routes=[route(port=8080)])
    rt.gateway.reconcile()
    container.remove.assert_called_once()
    assert client.containers.create.call_args.kwargs['labels'][REVISION] != saved['revision']
    save(gateway, enabled=False)
    rt.gateway.reconcile()
    assert container.stop.call_count == 2
    assert gateway.get()['credentials'] == credentials()


def test_foreign_container_and_error_redaction(store):
    client = MagicMock(); foreign = MagicMock(); foreign.labels = {}
    client.containers.get.return_value = foreign
    rt = Runtime(store, client)
    save(rt.gateway.store)
    rt.gateway.tick()
    assert rt.gateway.status()['state'] == 'error'
    foreign.stop.assert_not_called(); foreign.remove.assert_not_called()
    client.containers.get.side_effect = RuntimeError(credentials()['TunnelSecret'])
    rt.gateway.tick()
    assert credentials()['TunnelSecret'] not in json.dumps(rt.gateway.status())


def test_unconfigured_gateway_never_contacts_docker_and_shutdown_retains_data(store):
    client = MagicMock()
    rt = Runtime(store, client)
    rt.gateway.tick()
    client.containers.get.assert_not_called()
    save(rt.gateway.store)
    container = MagicMock(); container.labels = rt.labels(rt.gateway.store.get()['id'], 'gateway')
    client.containers.get.return_value = container
    rt.gateway.shutdown()
    container.stop.assert_called_once()
    assert rt.gateway.store.get()['enabled'] is True
