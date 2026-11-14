import pytest

from readiness import readiness


def ready_plex():
    return dict(desired=True, state='online', public_ip='185.111.109.2', public_port=12345,
                relay_enabled=True, relay_active=True, target_ready=True,
                internet_gateway={'enabled': True}, internet_status={'state': 'ready'},
                plex_sync={'enabled': True}, plex_status={'solo': {'state': 'ready'}})


@pytest.mark.parametrize('field,value', [
    ('state', 'connecting'), ('public_ip', ''), ('public_ip', '127.0.0.1'),
    ('public_port', 0), ('public_port', True), ('relay_active', False),
    ('target_ready', False), ('internet_status', {'state': 'pending'}),
    ('plex_status', {'solo': {'state': 'pending'}}),
    ('plex_status', {'solo': {'state': 'error'}}),
])
def test_healthy_tunnel_is_not_ready_until_every_enabled_part_works(field, value):
    row = ready_plex()
    assert readiness(row)['ready']
    row[field] = value
    result = readiness(row)
    assert not result['ready'] and result['waiting']


def test_gateway_waits_for_https_tcp_and_plex_checks():
    row = dict(desired=True, state='online', public_ip='185.111.109.2', public_port=12345,
               relay_active=True, gateway={'enabled': True, 'tcp': {'enabled': True,
                   'targets': [{'id': '1', 'name': 'Plex', 'plex_sync': True}]}},
               gateway_status={'state': 'pending'})
    assert len(readiness(row)['waiting']) == 3
    row.update(gateway_status={'state': 'ready'}, tcp_status={'1': {'state': 'ready'}},
               plex_status={'1': {'state': 'ready'}})
    assert readiness(row)['ready']


def test_optional_features_do_not_block_and_dns_sync_does():
    row = dict(desired=True, state='online', public_ip='185.111.109.2', public_port=12345)
    assert readiness(row)['ready']
    row['stream_sync'] = {'enabled': True}
    assert not readiness(row)['ready']
    row['stream_status'] = {'ok': True}
    assert readiness(row)['ready']
    row['desired'] = False
    assert not readiness(row)['ready']
