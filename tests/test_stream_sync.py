import json
from unittest.mock import Mock, patch

import pytest

from test_app import app, login, upload as base_upload, config


def upload(client, headers, **values):
    values.pop("name", None)
    return base_upload(client, headers, **values)
from stream_sync import StreamSync


def options():
    return dict(name='Video', host='192.168.1.110', port=443, relay_enabled=True,
        stream_sync=dict(enabled=True, fjordflix=True, domain='media.example.com', zone_id='a'*32),
        cloudflare_token='secret-token-for-test-only')


def test_secret_is_not_returned_and_blank_keeps_token(app):
    client, headers = login(app)
    response = upload(client, headers, **options())
    assert response.status_code == 201
    ident = response.json['id']
    store = app.extensions['store']
    assert 'secret-token' not in json.dumps(store.get(ident))
    listed = client.get('/api/profiles').json
    assert 'secret-token' not in json.dumps(listed)
    assert listed['profiles'][0]['cloudflare_token_saved'] is True
    changed = dict(options(), cloudflare_token='')
    assert client.post(f'/api/profiles/{ident}/settings', headers=headers, json=changed).status_code == 200
    assert (store.directory(ident)/'cloudflare-token').read_text() == options()['cloudflare_token']
    # No second writer competing over the single FjordFlix stream setting.
    assert upload(client, headers, key=2, **options()).status_code == 400


def test_requires_relay_and_valid_zone(app):
    client, headers = login(app)
    assert upload(client, headers, **dict(options(), relay_enabled=False)).status_code == 400
    bad = options(); bad['stream_sync']['zone_id'] = '../other-zone'
    assert upload(client, headers, **bad).status_code == 400
    assert app.extensions['store'].all() == []


def test_multiple_dns_profiles_are_independent_without_flix_or_relay(app):
    store = app.extensions['store']
    profiles = []
    for index, domain in enumerate(['vpn.example.com', 'game.example.com'], 1):
        values = options()
        values.update(relay_enabled=False, host='', port=None)
        values['stream_sync'].update(domain=domain, fjordflix=False)
        profiles.append(store.create(values, config(index)))
    sync = StreamSync(store, Mock())
    sync.dns = Mock(); sync.flix = Mock()
    for profile, address in zip(profiles, ['1.1.1.1', '8.8.8.8']):
        sync.sync(profile, dict(public_ip=address, public_port=''))
    sync.flix.assert_not_called()
    assert [(c.args[0]['domain'], c.args[2]) for c in sync.dns.call_args_list] == [
        ('vpn.example.com', '1.1.1.1'), ('game.example.com', '8.8.8.8')]
    with pytest.raises(ValueError, match='Domænet'):
        store.create(values, config(3))


def test_previous_stream_settings_keep_flix_mode(app):
    store = app.extensions['store']
    profile = store.create(options(), config())
    del profile['stream_sync']['fjordflix']
    store.save(profile)
    values = options(); del values['stream_sync']['fjordflix']
    assert store.edit(profile['id'], values)['stream_sync']['fjordflix'] is True


def test_dns_only_updates_exact_a_record():
    sync = StreamSync(Mock(), Mock())
    sync.cf = Mock(return_value=[dict(id='record', name='media.example.com', type='A', content='8.8.8.8', proxied=False)])
    sync.dns(options()['stream_sync'], 'secret', '8.8.8.8')
    assert sync.cf.call_count == 1
    sync.dns(options()['stream_sync'], 'secret', '1.1.1.1')
    assert sync.cf.call_args.args[1] == 'PATCH'
    assert sync.cf.call_args.kwargs['json'] == dict(content='1.1.1.1', proxied=False, ttl=60)
    sync.cf.reset_mock(); sync.cf.return_value = [dict(type='CNAME')]
    with pytest.raises(ValueError): sync.dns(options()['stream_sync'], 'secret', '1.1.1.1')
    assert sync.cf.call_count == 1


def test_port_changes_require_https_proof_and_keep_web_origin(app):
    store = app.extensions['store']
    profile = store.create(options(), config())
    profile = store.power(profile['id'], True)
    observed = dict(state='online', relay_active=True, public_ip='1.1.1.1', public_port=40000)
    runtime = Mock(); runtime.status.return_value = observed
    sync = StreamSync(store, runtime)
    sync.dns = Mock()
    sync.flix = Mock(return_value=dict(media='https://media.example.com:30000', web='https://film.example.com'))
    with patch('stream_sync.secrets.token_urlsafe', return_value='nonce'), patch('stream_sync.requests.Session') as session:
        response = session.return_value.__enter__.return_value.get.return_value
        response.status_code = 200; response.json.return_value = {'nonce':'wrong-server'}
        with pytest.raises(ValueError): sync.sync(profile, observed)
        assert not any(c.args[0]=='activate' for c in sync.flix.call_args_list)
        response.json.return_value = {'nonce':'nonce'}
        assert sync.sync(profile, observed) == 'https://media.example.com:40000'
        sync.flix.assert_called_with('activate','https://media.example.com:40000','https://film.example.com')
        assert session.return_value.__enter__.return_value.get.call_args.kwargs['allow_redirects'] is False


def test_offline_does_not_touch_dns_or_stream_settings(app):
    store = app.extensions['store']; store.create(options(), config())
    runtime = Mock(); runtime.status.return_value = dict(state='off',relay_active=False)
    sync = StreamSync(store,runtime); sync.sync = Mock()
    sync.tick()
    sync.sync.assert_not_called()
    assert 'Afventer' in next(iter(sync.statuses.values()))['message']


def test_ip_only_change_verifies_without_revoking_existing_streams(app):
    store = app.extensions['store']; profile = store.create(options(), config())
    observed = dict(state='online', relay_active=True, public_ip='1.1.1.1', public_port=40000)
    runtime = Mock(); runtime.status.return_value = observed
    sync = StreamSync(store, runtime); sync.dns = Mock()
    sync.flix = Mock(return_value=dict(media='https://media.example.com:40000', web='https://film.example.com'))
    with patch('stream_sync.secrets.token_urlsafe', return_value='nonce'), patch('stream_sync.requests.Session') as session:
        response = session.return_value.__enter__.return_value.get.return_value
        response.status_code = 200; response.json.return_value = {'nonce':'nonce'}
        sync.sync(profile, observed)
        sync.dns.assert_called_once()
        assert any(c.args[0]=='probe' for c in sync.flix.call_args_list)
        assert not any(c.args[0]=='activate' for c in sync.flix.call_args_list)
        runtime.status.return_value = dict(observed, public_port=50000)
        with pytest.raises(ValueError): sync.sync(profile, observed)
        assert not any(c.args[0]=='activate' for c in sync.flix.call_args_list)
