"""Read-only, secret-free discovery for a local Docker administrator (FjordSeed)."""
import json
import os
from pathlib import Path


def profiles(root):
    fields = ('id', 'name', 'desired', 'relay_enabled')
    result = []
    for path in sorted((Path(root) / 'profiles').glob('*/profile.json')):
        p = json.loads(path.read_text(encoding='utf-8'))
        item = {key: p[key] for key in fields}
        try:
            status = json.loads((path.parent/'observed/status.json').read_text())
            item['status'] = {key: status.get(key) for key in ('healthy','checked_at','public_ip','public_port')}
            item['status']['healthy'] = bool(status.get('healthy') and status.get('revision') == p.get('revision'))
        except (OSError, ValueError):
            item['status'] = {'healthy': False}
        result.append(item)
    return result


if __name__ == '__main__':
    print(json.dumps({'version': 1, 'profiles': profiles(os.environ.get('FJORDVPN_DATA', '/data'))}))
