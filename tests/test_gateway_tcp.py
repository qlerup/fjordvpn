import json
import struct
import time
from unittest.mock import Mock, MagicMock

import pytest

from gateway import settings, process_key, caddy_config
from relay import gateway_tcp as tcp
from runtime import Runtime
from test_gateway import gateway, NETWORKS, store_with_key
from test_app import config


TARGET = {'enabled': True, 'host': '192.168.1.96', 'port': 32400}


@pytest.mark.parametrize('field,value', [('enabled', 'yes'), ('host', '8.8.8.8'),
    ('host', '192.168.1.0'), ('host', ''), ('port', True), ('port', 0)])
def test_tcp_target_requires_allowed_lan_and_valid_port(field, value):
    with pytest.raises(ValueError):
        settings(gateway(tcp={**TARGET, field: value}), NETWORKS)


def test_tcp_is_opt_in_saved_to_observer_and_can_be_disabled(tmp_path):
    assert settings(gateway(), NETWORKS)['tcp'] == {'enabled': False}
    store, key = store_with_key(tmp_path)
    p = store.create({'name': 'Gateway', 'gateway': gateway(key, tcp=TARGET)}, config())
    path = store.directory(p['id']) / 'relay/settings.json'
    assert json.loads(path.read_text())['tcp'] == TARGET
    assert store.get(p['id'])['gateway']['tcp'] == TARGET
    store.edit(p['id'], {'name': p['name'], 'gateway': {**p['gateway'], 'tcp': {'enabled': False}}})
    assert json.loads(path.read_text())['tcp'] == {'enabled': False}


def test_request_checks_protocol_private_port_and_requested_public_port(monkeypatch):
    sock = MagicMock()
    sock.__enter__.return_value = sock
    monkeypatch.setattr(tcp.socket, 'socket', Mock(return_value=sock))
    sock.recv.return_value = struct.pack('!BBHIHHI', 0, 130, 0, 100, 56800, 62000, 60)
    assert tcp.request('tcp', 56800) == 62000
    sock.connect.assert_called_with(('10.2.0.1', 5351))
    assert sock.send.call_args.args[0] == struct.pack('!BBHHHI', 0, 2, 0, 56800, 0, 60)
    for data in (b'bad', struct.pack('!BBHIHHI', 0, 129, 0, 100, 56800, 62000, 60),
                 struct.pack('!BBHIHHI', 0, 130, 0, 100, 56801, 62000, 60)):
        sock.recv.return_value = data
        with pytest.raises(ValueError):
            tcp.request('tcp', 56800)


@pytest.fixture
def forwarder(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(tcp.time, 'monotonic', lambda: clock[0])
    request = Mock(return_value=62000)
    monkeypatch.setattr(tcp, 'request', request)
    process = Mock(); process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr(tcp.subprocess, 'Popen', popen)
    stop = Mock()
    f = tcp.Forwarder(stop)
    return f, request, popen, stop, clock


def update(f, **changes):
    values = dict(config=TARGET, address='185.111.109.2', gateway_port=63503, revision='r1', healthy=True)
    return f.update(**{**values, **changes})


def test_independent_mapping_renews_and_target_changes_stop_old_connections(forwarder):
    f, request, popen, stop, clock = forwarder
    assert update(f)['public_port'] == 62000
    assert [c.args for c in request.call_args_list] == [('udp', 56800, 0), ('tcp', 56800, 62000)]
    assert 'TCP4-LISTEN:56800,reuseaddr,fork,nodelay' in popen.call_args.args[0]
    assert 'TCP4:192.168.1.96:32400,connect-timeout=5,nodelay' in popen.call_args.args[0]
    clock[0] = 10
    assert update(f)['state'] == 'ready' and request.call_count == 2
    clock[0] = 21
    assert update(f)['state'] == 'ready' and request.call_count == 4
    assert update(f, config={**TARGET, 'host': '192.168.1.97'}, revision='r2')['state'] == 'ready'
    assert popen.call_count == 2
    assert ('udp', 56800, 62000, 0) in [c.args for c in request.call_args_list]
    stop.assert_any_call(popen.return_value)


@pytest.mark.parametrize('change', [{'healthy': False}, {'address': ''}, {'config': {'enabled': False}}])
def test_loss_of_vpn_or_disable_closes_only_owned_mapping(forwarder, change):
    f, request, popen, stop, clock = forwarder
    update(f)
    assert update(f, **change)['public_port'] is None
    assert f.process is None and f.public == 0
    assert all(c.args[1] == 56800 for c in request.call_args_list)
    assert [c.args for c in request.call_args_list[-2:]] == [('udp', 56800, 62000, 0), ('tcp', 56800, 62000, 0)]


def test_renewal_failure_and_dead_process_are_never_ready(forwarder):
    f, request, popen, stop, clock = forwarder
    update(f); clock[0] = 51
    request.side_effect = TimeoutError()
    assert update(f)['state'] == 'error' and f.public == 0
    stop.assert_any_call(popen.return_value)
    request.side_effect = None
    popen.return_value.poll.return_value = 1
    assert update(f)['state'] == 'error'


@pytest.mark.parametrize('ports', [(62000, 62001), (63503, 63503), (56800, 56800), (56789, 56789)])
def test_mapping_conflicts_do_not_start_relay(forwarder, ports):
    f, request, popen, stop, clock = forwarder
    replies = iter(ports)
    request.side_effect = lambda *args: 0 if len(args) == 4 else next(replies)
    assert update(f)['state'] == 'error'
    popen.assert_not_called()


def test_private_port_avoids_primary_gateway_listener(forwarder):
    f, request, popen, stop, clock = forwarder
    assert update(f, gateway_port=56800)['state'] == 'ready'
    assert f.private == 56801


def test_unrelated_profile_revision_does_not_restart_tcp_connections(forwarder):
    f, request, popen, stop, clock = forwarder
    update(f)
    stop.reset_mock()
    assert update(f, revision='changed-web-settings')['state'] == 'ready'
    assert popen.call_count == 1 and request.call_count == 2
    stop.assert_not_called()


def test_tcp_edits_do_not_restart_or_modify_web_and_minecraft():
    data = gateway(minecraft={'enabled': True, 'domain': 'mc.example.com',
        'host': '192.168.1.152', 'port': 25566})
    original = settings(data, NETWORKS)
    identity = process_key(original, 63503, b'token')
    caddy = caddy_config(original['routes'], 63503, minecraft=original['minecraft'])
    for target in (TARGET, {**TARGET, 'host': '192.168.1.97'}, {'enabled': False}):
        changed = settings({**data, 'tcp': target}, NETWORKS)
        assert process_key(changed, 63503, b'token') == identity
        assert caddy_config(changed['routes'], 63503, minecraft=changed['minecraft']) == caddy
    changed['routes'][0]['port'] = 8100
    assert process_key(changed, 63503, b'token') != identity


def test_firewall_is_tagged_tcp_only_idempotent_and_removable(tmp_path):
    rt = Runtime(Mock(), MagicMock())
    vpn = Mock()
    vpn.exec_run.side_effect = [Mock(exit_code=1), Mock(exit_code=0)] * 2
    p = {'id': 'a'*32, 'gateway': {'tcp': TARGET}}
    (tmp_path/'observed').mkdir()
    rt.tcp_firewall(vpn, p, tmp_path)
    writes = [c.args[0] for c in vpn.exec_run.call_args_list if '-I' in c.args[0]]
    assert len(writes) == 2
    assert all('tun0' in c and 'tcp' in c and 'fjordvpn-tcp-'+p['id'] in c for c in writes)
    assert all('udp' not in c and '63503' not in c for c in writes)
    vpn.exec_run.reset_mock(); vpn.exec_run.side_effect = None; vpn.exec_run.return_value = Mock(exit_code=0)
    rt.tcp_firewall(vpn, p, tmp_path)
    assert all('-C' in c.args[0] for c in vpn.exec_run.call_args_list)
    p['gateway']['tcp'] = {'enabled': False}
    vpn.exec_run.reset_mock()
    rt.tcp_firewall(vpn, p, tmp_path)
    assert len([c for c in vpn.exec_run.call_args_list if '-D' in c.args[0]]) == 2
    assert not (tmp_path/'observed/tcp-firewall').exists()


@pytest.mark.parametrize('change,ready', [({}, True), ({'checked_at': 0}, False),
    ({'revision': 'old'}, False), ({'healthy': False}, False),
    ({'tcp_status': {'state': 'ready', 'public_port': True}}, False)])
def test_runtime_does_not_publish_stale_tcp_endpoint(tmp_path, change, ready):
    store, key = store_with_key(tmp_path)
    p = store.create({'name': 'Gateway', 'gateway': gateway(key, tcp=TARGET)}, config())
    p = store.power(p['id'], True)
    observed = dict(checked_at=time.time(), revision=p['revision'], healthy=True,
                    public_ip='185.111.109.2', public_port=63503, relay_active=True,
                    message='HTTPS active', tcp_status={'state': 'ready', 'public_port': 62000})
    observed.update(change)
    (store.directory(p['id'])/'observed/status.json').write_text(json.dumps(observed))
    rt = Runtime(store, MagicMock())
    rt.client.containers.get.return_value = Mock(status='running', labels={
        'dk.fjordvpn.managed':'1', 'dk.fjordvpn.profile':p['id']})
    rt.client.containers.list.return_value = []
    status = rt.status(p)['tcp_status']
    assert (status['state'] == 'ready') is ready
    assert (status['public_port'] == 62000) is ready
