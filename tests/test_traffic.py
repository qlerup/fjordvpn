import json
import subprocess
from types import SimpleNamespace

import pytest

from relay.traffic import TrafficMonitor


def monitor(tmp_path):
    (tmp_path / 'statistics').mkdir()
    (tmp_path / 'ifindex').write_text('4')
    state = {'acked': 1000}

    def run(command, **kwargs):
        if command[0] == 'ip':
            return SimpleNamespace(stdout=json.dumps([{'addr_info': [{'local': '10.2.0.2', 'family': 'inet'}]}]))
        return SimpleNamespace(stdout=f'0 500 10.2.0.2:12345 203.0.113.1:1234\n\t cubic bytes_acked:{state["acked"]} bytes_sent:999999\n')

    def counters(rx, tx):
        (tmp_path / 'statistics/rx_bytes').write_text(str(rx))
        (tmp_path / 'statistics/tx_bytes').write_text(str(tx))

    counters(0, 0)
    return TrafficMonitor(tmp_path, run), state, counters


def test_tunnel_direction_and_acknowledged_delivery(tmp_path):
    m, tcp, counters = monitor(tmp_path)
    assert m.sample(10, 100)['state'] == 'warming'
    counters(2_000_000, 10_000_000)
    tcp['acked'] += 8_000_000
    result = m.sample(12, 102)
    assert result['download_mbps'] == 8
    assert result['upload_mbps'] == 40
    assert result['delivered_mbps'] == 32
    assert '203.0.113.1' not in json.dumps(result)


@pytest.mark.parametrize('reset', ['counter', 'interface', 'time'])
def test_restarts_resets_and_stale_samples_do_not_create_spikes(tmp_path, reset):
    m, tcp, counters = monitor(tmp_path)
    counters(1000, 1000)
    m.sample(10, 100)
    counters(2000, 2000)
    m.sample(12, 102)
    if reset == 'counter':
        counters(0, 0)
    elif reset == 'interface':
        (tmp_path / 'ifindex').write_text('8')
    result = m.sample(30 if reset == 'time' else 14, 104)
    assert result == {'state': 'warming', 'history': []}


def test_missing_interface_and_tcp_tools_are_not_zero_capacity(tmp_path):
    m, tcp, counters = monitor(tmp_path)
    def unavailable(*args, **kwargs):
        raise subprocess.TimeoutExpired('ss', 1)
    m.run = unavailable
    m.sample(10, 100)
    assert m.sample(12, 102)['delivered_mbps'] is None
    (tmp_path / 'ifindex').unlink()
    assert m.sample(14, 104) == {'state': 'unavailable', 'history': []}


def test_history_is_bounded_and_new_connections_need_a_baseline(tmp_path):
    m, tcp, counters = monitor(tmp_path)
    for i in range(200):
        counters(i * 1_000_000, i * 2_000_000)
        result = m.sample(10 + i * 2, 100 + i * 2)
    assert len(result['history']) == 150
    assert result['delivered_mbps'] == 0


def test_api_uses_server_clock_for_measurement_freshness(tmp_path, monkeypatch):
    from runtime import Runtime
    runtime = Runtime.__new__(Runtime)
    runtime.store = SimpleNamespace(directory=lambda ident: tmp_path)
    runtime.container = lambda *args: SimpleNamespace(status='running')
    runtime.errors = {}
    runtime.seedbox_status = lambda *args: None
    runtime.fjorddrop_status = lambda *args: []
    monkeypatch.setattr('runtime.time.time', lambda: 1000)
    (tmp_path / 'observed').mkdir()
    report = dict(checked_at=999, healthy=True, public_ip='185.111.109.2',
                  public_port=12345, relay_active=False, message='VPN klar', revision='a',
                  traffic=dict(state='ready', at=998, history=[]))
    path = tmp_path / 'observed/status.json'
    path.write_text(json.dumps(report))
    profile = dict(id='test', desired=True, revision='a')
    assert runtime.status(profile)['traffic']['age_seconds'] == 2
    report['checked_at'] = 900
    path.write_text(json.dumps(report))
    assert 'traffic' not in runtime.status(profile)
