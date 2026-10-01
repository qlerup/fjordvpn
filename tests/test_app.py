import base64
import io
import ipaddress
import json
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import docker
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from configuration import parse_wireguard
from runtime import Runtime, LABEL, OWNER
from store import Store


def config(key=1):
    private=base64.b64encode(bytes([key])*32).decode()
    public=base64.b64encode(bytes([9])*32).decode()
    return f'[Interface]\nPrivateKey = {private}\nAddress = 10.2.0.2/32, fd00::2/128\nDNS = 10.2.0.1\n\n[Peer]\nPublicKey = {public}\nAllowedIPs = 0.0.0.0/0, ::/0\nEndpoint = 185.159.157.1:51820\n'.encode()


class FakeRuntime:
    def __init__(self,store):self.store=store
    def status(self,p):return {k:v for k,v in dict(p,state='off',public_ip='',public_port='',message='Slukket').items() if k!='fingerprint'}


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path,testing=True,runtime_factory=FakeRuntime)


def login(app):
    c=app.test_client()
    c.get('/login')
    with c.session_transaction() as s:csrf=s['csrf']
    pw=(app.extensions['store'].root/'initial-login.txt').read_text(encoding='utf-8').splitlines()[1].split(': ',1)[1]
    r=c.post('/login',data={'username':'admin','password':pw,'csrf':csrf})
    assert r.status_code==302
    with c.session_transaction() as s:csrf=s['csrf']
    return c,{'X-CSRF-Token':csrf}


def upload(c,h,key=1,**settings):
    return c.post('/api/profiles',headers=h,data={'settings':json.dumps(dict(name='Film',**settings)),
        'config':(io.BytesIO(config(key)),'vpn.conf')})


def test_auth_csrf_and_host(app):
    c=app.test_client()
    assert c.get('/api/profiles').status_code==401
    assert c.get('/login',headers={'Host':'evil.test'}).status_code==403
    assert c.post('/login',data={'username':'admin'}).status_code==403
    c,h=login(app)
    assert c.post('/api/profiles').status_code==403
    assert c.get('/api/profiles').json=={'profiles':[]}
    assert c.get('/').headers['X-Frame-Options']=='DENY'
    assert 'legacy' not in app.extensions


def test_upload_multiple_keep_secrets_server_side(app):
    c,h=login(app)
    a=upload(c,h,1,start=True);b=upload(c,h,2)
    assert a.status_code==b.status_code==201
    rows=c.get('/api/profiles').json['profiles']
    assert len(rows)==2
    assert all('fingerprint' not in p and 'PrivateKey' not in json.dumps(p) for p in rows)
    assert app.extensions['store'].get(a.json['id'])['desired'] is True
    assert app.extensions['store'].get(b.json['id'])['desired'] is False
    assert upload(c,h,1).status_code==400
    assert len(app.extensions['store'].all())==2


@pytest.mark.parametrize('addition',[b'PostUp = touch /tmp/oops\n',b'PreDown = reboot\n',b'Address = 10.0.0.2/32\n'])
def test_reject_hooks_and_duplicate_config(addition):
    raw=config().replace(b'[Peer]',addition+b'\n[Peer]')
    with pytest.raises(ValueError):parse_wireguard(raw)


def test_normalizes_ipv4_and_rejects_invalid_keys():
    normalized,meta=parse_wireguard(config())
    assert '::' not in normalized
    assert meta['ipv6_removed']
    with pytest.raises(ValueError):parse_wireguard(config().replace(b'PrivateKey = ',b'PrivateKey = nope'))
    with pytest.raises(ValueError):parse_wireguard(config().replace(b'185.159.157.1',b'127.0.0.1'))
    with pytest.raises(ValueError):parse_wireguard(b'x'*16385)


def test_edit_target_validation_and_persistence(app):
    c,h=login(app);ident=upload(c,h).json['id']
    for host,port in [('1.1.1.1',443),('192.168.1.255',80),('192.168.1.1',True),('192.168.1.1',0)]:
        assert c.post(f'/api/profiles/{ident}/settings',headers=h,json={'name':'Test','host':host,'port':port,'relay_enabled':True}).status_code==400
    assert c.post(f'/api/profiles/{ident}/settings',headers=h,json={'name':'HTTPS','host':'192.168.1.110','port':443,'relay_enabled':True}).status_code==200
    path=app.extensions['store'].directory(ident)
    assert json.loads((path/'relay/settings.json').read_text())['enabled'] is True
    assert c.post(f'/api/profiles/{ident}/power',headers=h,json={'enabled':True}).status_code==200
    fresh=Store(app.extensions['store'].root,[ipaddress.ip_network('192.168.1.0/24')])
    assert fresh.get(ident)['desired'] is True
    assert c.post(f'/api/profiles/{ident}/power',headers=h,json={'enabled':False}).status_code==200
    assert (path/'wg0.conf').exists()


def test_bad_ids_and_invalid_json(app):
    c,h=login(app)
    assert c.post('/api/profiles/not-a-profile/power',headers=h,json={'enabled':True}).status_code==400
    assert c.post('/api/profiles/'+'0'*32+'/power',headers=h,json={'enabled':True}).status_code==400
    assert c.post('/api/profiles/'+'0'*32+'/power',headers=h,json=[]).status_code==400
    assert c.post('/api/profiles',headers=h,data={'settings':'[]','config':(io.BytesIO(config()),'x.conf')}).status_code==400


def test_simultaneous_duplicate_upload_is_atomic(tmp_path):
    store=Store(tmp_path,[ipaddress.ip_network('192.168.1.0/24')])
    outcomes=[]
    def create():
        try:store.create({'name':'same'},config());outcomes.append(True)
        except ValueError:outcomes.append(False)
    a,b=threading.Thread(target=create),threading.Thread(target=create)
    a.start();b.start();a.join();b.join()
    assert sorted(outcomes)==[False,True]
    assert len(store.all())==1


def test_docker_ownership_and_no_foreign_mutation(tmp_path):
    store=Store(tmp_path,[ipaddress.ip_network('192.168.1.0/24')]);p=store.create({'name':'test'},config())
    client=MagicMock();foreign=MagicMock();foreign.labels={};client.containers.get.return_value=foreign
    rt=Runtime(store,client)
    with pytest.raises(ValueError):rt.reconcile(p)
    foreign.stop.assert_not_called();foreign.remove.assert_not_called();foreign.start.assert_not_called()


def test_docker_start_is_isolated_and_stop_retains_config(tmp_path,monkeypatch):
    import runtime
    monkeypatch.setattr(runtime.os,'getuid',lambda:1000,raising=False)
    monkeypatch.setattr(runtime.os,'getgid',lambda:1000,raising=False)
    store=Store(tmp_path,[ipaddress.ip_network('192.168.1.0/24')]);p=store.create({'name':'test'},config());p['desired']=True
    client=MagicMock();client.containers.get.side_effect=docker.errors.NotFound('no')
    vpn=MagicMock();vpn.id='new-vpn';vpn.status='created';relay=MagicMock();relay.status='created'
    client.containers.create.side_effect=[vpn,relay]
    rt=Runtime(store,client);rt.reconcile(p)
    vpn.start.assert_called_once();relay.start.assert_called_once()
    calls=client.containers.create.call_args_list
    assert calls[0].kwargs['labels']=={OWNER:'1',LABEL:p['id']}
    assert 'ports' not in calls[0].kwargs
    assert calls[1].kwargs['network_mode']=='container:new-vpn'
    assert calls[1].kwargs['cap_drop']==['ALL']
    assert calls[0].kwargs['environment']['VPN_PORT_FORWARDING']=='on'
    vpn.labels=relay.labels={OWNER:'1',LABEL:p['id']};vpn.status=relay.status='running'
    client.containers.get.side_effect=[vpn,relay]
    p['desired']=False;rt.reconcile(p)
    vpn.stop.assert_called_once();relay.stop.assert_called_once()
    vpn.remove.assert_not_called()
    assert (store.directory(p['id'])/'wg0.conf').exists()
