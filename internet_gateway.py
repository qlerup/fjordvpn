"""Private WireGuard link from a LAN server into its dedicated Proton namespace."""
import base64
import ipaddress
import json
import subprocess

PORT = 51821
INTERFACE = 'fjinternet'


def keypair():
    private = subprocess.check_output(['wg', 'genkey']).decode().strip()
    public = subprocess.check_output(['wg', 'pubkey'], input=(private+'\n').encode()).decode().strip()
    for value in (private, public):
        if len(base64.b64decode(value, validate=True)) != 32:
            raise ValueError('WireGuard-nøgler kunne ikke oprettes.')
    return private, public


def settings(value, target, networks, previous=None, used=()):
    if not isinstance(value, dict) or type(value.get('enabled', False)) is not bool:
        raise ValueError('Ugyldigt valg af internetgateway.')
    previous = previous or {}
    if not value.get('enabled'):
        return dict(previous, enabled=False)
    if not target['relay_enabled']:
        raise ValueError('Internetgateway kræver aktiv TCP-videresendelse til serveren.')
    if previous.get('subnet'):
        return dict(previous, enabled=True)
    for pool in ('10.250.0.0/16', '10.251.0.0/16', '10.252.0.0/16'):
        network = ipaddress.IPv4Network(pool)
        if any(network.overlaps(n) for n in networks):
            continue
        for subnet in network.subnets(new_prefix=30):
            if str(subnet) not in used:
                return {'enabled': True, 'subnet': str(subnet)}
    raise ValueError('Der kunne ikke findes et privat net til WireGuard-relæet.')


def addresses(profile):
    network = ipaddress.IPv4Network(profile['internet_gateway']['subnet'])
    return str(network[1]), str(network[2])


def signature(profile):
    config = profile.get('internet_gateway', {})
    return json.dumps([config, profile.get('host')], sort_keys=True) if config.get('enabled') else ''


def private_rule_present(rules, subnet):
    for rule in rules:
        if rule.get('priority') != 97 or rule.get('table') not in ('main', 254):
            continue
        destination = rule.get('dst', '')
        try:
            network = ipaddress.IPv4Network(destination if '/' in destination else
                (destination, rule.get('dstlen', 32)), strict=False)
        except ValueError:
            continue
        if network == ipaddress.IPv4Network(subnet) and rule.get('src', 'all') == 'all':
            return True
    return False


def firewall_rules(profile):
    _, client = addresses(profile)
    source = client+'/32'
    comment = ['-m', 'comment', '--comment', 'fjordvpn-internet-'+profile['id']]
    return [
        ('filter', 'FORWARD', '-A', ['-s', source, *comment, '-j', 'DROP']),
        ('filter', 'FORWARD', '-A', ['-d', source, *comment, '-j', 'DROP']),
        ('filter', 'FORWARD', '-I', ['-i', INTERFACE, '-s', source, '-o', 'tun0', *comment, '-j', 'ACCEPT']),
        ('filter', 'FORWARD', '-I', ['-i', 'tun0', '-o', INTERFACE, '-d', source, '-m', 'conntrack',
                                    '--ctstate', 'ESTABLISHED,RELATED', *comment, '-j', 'ACCEPT']),
        # Docker's UDP proxy may rewrite the source after a restart. WireGuard
        # authenticates the sole configured peer cryptographically, not by LAN IP.
        ('filter', 'INPUT', '-I', ['-i', 'eth0', '-p', 'udp', '--dport', str(PORT),
                                  *comment, '-j', 'ACCEPT']),
        ('filter', 'OUTPUT', '-I', ['-o', INTERFACE, '-d', source, *comment, '-j', 'ACCEPT']),
        ('nat', 'POSTROUTING', '-A', ['-s', source, '-o', 'tun0', *comment, '-j', 'MASQUERADE']),
        ('mangle', 'FORWARD', '-A', ['-s', source, '-o', 'tun0', '-p', 'tcp', '--tcp-flags', 'SYN,RST',
                                   'SYN', *comment, '-j', 'TCPMSS', '--clamp-mss-to-pmtu']),
    ]


def client_config(profile, folder, networks):
    """Linux wg-quick profile. The output guard intentionally survives wg-quick down."""
    from pathlib import Path
    folder = Path(folder)
    if not config_ready(folder):
        raise ValueError('WireGuard-konfigurationen er ikke klar.')
    runtime = json.loads((folder/'internet-runtime.json').read_text())
    keys = json.loads((folder/'internet-public.json').read_text())
    _, client = addresses(profile)
    chain = 'FJVPN_'+profile['id'][:8]
    lines = ['[Interface]', 'PrivateKey = '+(folder/'internet-client.key').read_text().strip(),
             'Address = '+client+'/32', 'MTU = 1280', 'DNS = 1.1.1.1, 9.9.9.9']
    for tool in ('iptables', 'ip6tables'):
        rules = [['-o', 'lo', '-j', 'ACCEPT'], ['-o', '%i', '-j', 'ACCEPT']]
        if tool == 'iptables':
            rules += [['-p', protocol, '--dport', '53', '-j', 'REJECT'] for protocol in ('udp','tcp')]
        rules += [['-d', str(n), '-j', 'ACCEPT'] for n in networks] if tool == 'iptables' else [['-d', 'fe80::/10', '-j', 'ACCEPT']]
        rules += [['-j', 'REJECT']]
        # Build the chain before attaching it. No down hook removes the guard.
        body = '; '.join(tool+' -A '+chain+' '+' '.join(rule) for rule in rules)
        lines.append('PostUp = '+tool+' -N '+chain+' 2>/dev/null && { '+body+'; } || '+tool+' -L '+chain+' -n >/dev/null')
        lines.append('PostUp = '+tool+' -C OUTPUT -j '+chain+' 2>/dev/null || '+tool+' -I OUTPUT -j '+chain)
    lines += ['', '[Peer]', 'PublicKey = '+keys['server'],
              'Endpoint = '+runtime['host']+':'+str(runtime['port']),
              'AllowedIPs = 0.0.0.0/0', 'PersistentKeepalive = 25', '']
    return '\n'.join(lines)


def config_ready(folder):
    from pathlib import Path
    folder = Path(folder)
    try:
        runtime = json.loads((folder/'internet-runtime.json').read_text())
        ipaddress.IPv4Address(runtime.get('host',''))
        return (type(runtime.get('port')) is int and 1 <= runtime['port'] <= 65535 and
                all((folder/name).is_file() for name in ('internet-client.key','internet-public.json')))
    except (OSError, ValueError, TypeError):
        return False
