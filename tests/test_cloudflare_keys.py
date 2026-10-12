import json
import pytest
from test_app import app, login, config
from test_stream_sync import options
from store import Store


def test_key_api_never_returns_token_and_rotates_shared_key(app):
    client, headers = login(app)
    values=dict(name='My zone',zone_id='a'*32,token='secret-token-for-test-only')
    assert client.post('/api/cloudflare-keys',json=values).status_code == 403
    response=client.post('/api/cloudflare-keys',json=values,headers=headers)
    assert response.status_code == 200
    ident=response.json['id']; store=app.extensions['store']
    assert 'secret-token' not in json.dumps(client.get('/api/cloudflare-keys').json)
    profiles=[]
    for i in (1,2):
        data=options(); data.pop('cloudflare_token')
        data['stream_sync']=dict(enabled=True,key_id=ident,domain=f'vpn{i}.example.com',fjordflix=False)
        profiles.append(store.create(data,config(i)))
    assert all(store.sync_token(p)==values['token'] for p in profiles)
    assert client.post('/api/cloudflare-keys/'+ident,json=dict(values,token='rotated-token-for-test-only'),headers=headers).status_code==200
    assert all(store.sync_token(p)=='rotated-token-for-test-only' for p in profiles)
    assert client.post('/api/cloudflare-keys/'+ident,json=dict(values,token='',name='Renamed'),headers=headers).status_code==200
    assert store.key(ident)['token']=='rotated-token-for-test-only'
    assert client.post('/api/cloudflare-keys/'+ident,json=dict(values,zone_id='b'*32),headers=headers).status_code==400


def test_legacy_keys_migrate_idempotently(app):
    store=app.extensions['store']; profile=store.create(options(),config())
    again=Store(store.root,store.networks)
    saved=again.get(profile['id'])
    assert saved['stream_sync']['key_id']
    assert again.sync_token(saved)==options()['cloudflare_token']
    assert len(again.keys())==1
    assert len(Store(store.root,store.networks).keys())==1


def test_unknown_key_rejected(app):
    store=app.extensions['store']; data=options()
    data['stream_sync']['key_id']='b'*32
    with pytest.raises(ValueError): store.create(data,config())
