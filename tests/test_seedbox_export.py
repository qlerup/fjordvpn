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
