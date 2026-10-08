import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from hub import Hub, HubError
from test_app import FakeRuntime

ADMIN={'id':7,'username':'test-admin','role':'admin','hub_role':'user','must_change_password':False}


@pytest.fixture
def managed(tmp_path,monkeypatch):
    monkeypatch.setenv('FJORDHUB_URL','http://hub:8091')
    monkeypatch.setenv('FJORDHUB_API_KEY','test-key-not-a-real-secret')
    return create_app(tmp_path,testing=True,runtime_factory=FakeRuntime)


def test_sso_login_and_revoked_access(managed):
    hub=managed.extensions['hub']
    hub.call=Mock(side_effect=[dict(ADMIN,ok=True),{'ok':True,'items':[ADMIN]}])
    client=managed.test_client()
    result=client.get('/hub-login?token=single-use-test')
    assert result.status_code==302 and result.location=='/'
    cookie = result.headers['Set-Cookie']
    assert cookie.startswith('fjordvpn_session=')
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie
    with client.session_transaction() as s:
        assert s['hub_uid']==7
        assert 'single-use-test' not in json.dumps(dict(s))
    assert client.get('/api/profiles').status_code==200
    assert client.get('/api/auth/access').json['authenticated'] is True
    hub.expires=0
    hub.call=Mock(return_value={'ok':True,'items':[]})
    revoked=client.get('/api/profiles')
    assert revoked.status_code==401 and revoked.json['error_code']=='access_revoked'
    assert client.get('/api/auth/access').json['error_code']=='access_revoked'
    assert client.get('/api/profiles').status_code==401


def test_regular_user_cannot_get_admin_control(managed):
    managed.extensions['hub'].call=Mock(return_value=dict(ADMIN,ok=True,role='user'))
    client=managed.test_client()
    assert client.get('/hub-login?token=test').status_code==403
    assert client.get('/api/profiles').status_code==401


def test_partial_hub_config_does_not_enable_local_fallback(tmp_path,monkeypatch):
    monkeypatch.setenv('FJORDHUB_URL','http://hub')
    monkeypatch.delenv('FJORDHUB_API_KEY',raising=False)
    app=create_app(tmp_path,testing=True,runtime_factory=FakeRuntime)
    assert not (tmp_path/'initial-login.txt').exists()
    assert json.loads((tmp_path/'auth.json').read_text())['password_hash']==''
    c=app.test_client();c.get('/login')
    with c.session_transaction() as s:csrf=s['csrf']
    result=c.post('/login',data={'username':'admin','password':'anything','csrf':csrf})
    assert result.status_code==200
    assert c.get('/api/profiles').status_code==401


def test_hub_outage_and_temporary_password_fail_closed(managed):
    hub=managed.extensions['hub']
    hub.call=Mock(side_effect=[dict(ADMIN,ok=True),{'ok':True,'items':[ADMIN]}])
    c=managed.test_client();c.get('/hub-login?token=test')
    hub.expires=0;hub.call=Mock(side_effect=HubError('Hub unavailable',503))
    assert c.get('/api/profiles').status_code==503
    hub.call=Mock(return_value={'ok':True,'items':[dict(ADMIN,must_change_password=True)]})
    assert c.get('/api/profiles').status_code==403


def test_managed_password_login(managed):
    hub=managed.extensions['hub']
    hub.call=Mock(side_effect=[{'ok':True,'user':ADMIN},{'ok':True,'items':[ADMIN]}])
    c=managed.test_client();c.get('/login')
    with c.session_transaction() as s:csrf=s['csrf']
    assert c.post('/login',data={'username':'test-admin','password':'test','csrf':csrf}).status_code==302
    assert c.get('/api/profiles').status_code==200
