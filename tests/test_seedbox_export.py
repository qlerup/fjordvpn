import json
from seedbox_export import profiles


def test_export_never_includes_secrets_and_requires_current_status(tmp_path):
    folder=tmp_path/'profiles'/('a'*32)
    (folder/'observed').mkdir(parents=True)
    profile={'id':'a'*32,'name':'Seed','desired':True,'relay_enabled':False,'revision':'new',
             'fingerprint':'private-fingerprint','private_key':'never-export'}
    (folder/'profile.json').write_text(json.dumps(profile))
    (folder/'wg0.conf').write_text('PrivateKey=never-export')
    (folder/'observed/status.json').write_text(json.dumps({'healthy':True,'revision':'old','checked_at':100,'public_port':40001}))
    result=profiles(tmp_path)
    assert result[0]['status']['healthy'] is False
    assert 'never-export' not in json.dumps(result) and 'fingerprint' not in json.dumps(result)
    (folder/'observed/status.json').unlink()
    assert profiles(tmp_path)[0]['status']=={'healthy':False}


def test_gateway_export_adds_safe_capability_and_keeps_old_clients_blocked(tmp_path):
    folder=tmp_path/'profiles'/('b'*32);folder.mkdir(parents=True)
    (folder/'profile.json').write_text(json.dumps({'id':'b'*32,'name':'Gateway','desired':True,
        'relay_enabled':False,'gateway':{'enabled':True,'key_id':'private-reference','routes':[]}}))
    row=profiles(tmp_path)[0]
    assert row['gateway_enabled'] is True and row['relay_enabled'] is True
    assert 'private-reference' not in json.dumps(row) and 'gateway' not in row


def test_gateway_route_edit_does_not_stop_independent_torrent_tunnel(tmp_path):
    folder=tmp_path/'profiles'/('c'*32);(folder/'observed').mkdir(parents=True)
    (folder/'profile.json').write_text(json.dumps({'id':'c'*32,'name':'Gateway','desired':True,
        'relay_enabled':False,'revision':'new-routes','gateway':{'enabled':True}}))
    path=folder/'observed/status.json'
    path.write_text(json.dumps({'healthy':True,'revision':'old-routes','checked_at':100,'public_port':53289}))
    assert profiles(tmp_path)[0]['status']['healthy'] is True
    path.write_text(json.dumps({'healthy':False,'revision':'old-routes','checked_at':100,'public_port':53289}))
    assert profiles(tmp_path)[0]['status']['healthy'] is False
