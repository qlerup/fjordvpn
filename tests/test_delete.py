from unittest.mock import MagicMock

import docker
import pytest

from test_app import app, login, upload, config
from runtime import Runtime, LABEL, OWNER


def test_delete_requires_auth_csrf_confirmation_and_stopped_profile(app):
    c,h=login(app)
    ident=upload(c,h,start=True).json['id']
    path=f'/api/profiles/{ident}/delete'
    assert app.test_client().post(path,json={'confirm':True},headers=h).status_code==403
    assert c.post(path,json={'confirm':True}).status_code==403
    assert c.post(path,json={},headers=h).status_code==400
    app.extensions['runtime'].remove=MagicMock()
    assert c.post(path,json={'confirm':True},headers=h).status_code==400
    app.extensions['runtime'].remove.assert_not_called()
    assert app.extensions['store'].get(ident)['desired']


def test_delete_preserves_other_profiles_shared_keys_and_dns(app):
    c,h=login(app);store=app.extensions['store']
    ident=upload(c,h).json['id'];other=store.create({'name':'Other'},config(2))
    key=store.save_key({'name':'Shared','zone_id':'a'*32,'token':'b'*40})
    other_data=(store.directory(other['id'])/'profile.json').read_bytes()
    app.extensions['runtime'].remove=MagicMock()
    app.extensions['gateway_sync'].cf=MagicMock()
    assert c.post(f'/api/profiles/{ident}/delete',headers=h,json={'confirm':True}).status_code==200
    assert not store.directory(ident).exists()
    assert (store.directory(other['id'])/'profile.json').read_bytes()==other_data
    assert store.key(key)['token']=='b'*40
    app.extensions['gateway_sync'].cf.assert_not_called()
    assert c.post(f'/api/profiles/{ident}/delete',headers=h,json={'confirm':True}).status_code==400
    assert c.post('/api/profiles/../delete',headers=h,json={'confirm':True}).status_code in (400,404)


def test_failed_runtime_delete_retains_profile_and_hides_docker_details(app):
    c,h=login(app);ident=upload(c,h).json['id']
    app.extensions['runtime'].remove=MagicMock(side_effect=docker.errors.DockerException('private config'))
    r=c.post(f'/api/profiles/{ident}/delete',headers=h,json={'confirm':True})
    assert r.status_code==503 and 'private config' not in r.get_data(as_text=True)
    assert app.extensions['store'].get(ident)


@pytest.mark.parametrize('foreign,running',[(False,False),(True,False),(False,True)])
def test_runtime_delete_checks_both_owners_and_states_before_mutating(app,foreign,running):
    store=app.extensions['store'];p=store.create({'name':'Old'},config())
    client=MagicMock();relay=MagicMock();vpn=MagicMock()
    relay.labels={OWNER:'1',LABEL:p['id']};vpn.labels={OWNER:'1',LABEL:p['id'] if not foreign else 'other'}
    relay.status='exited';vpn.status='running' if running else 'exited'
    client.containers.get.side_effect=[relay,vpn]
    runtime=Runtime(store,client=client)
    if foreign or running:
        with pytest.raises(ValueError):runtime.remove(p)
        relay.remove.assert_not_called();vpn.remove.assert_not_called()
    else:
        runtime.remove(p)
        relay.remove.assert_called_once_with();vpn.remove.assert_called_once_with()
