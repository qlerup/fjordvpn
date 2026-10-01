"""Strict WireGuard import. Uploaded files are data, never shell scripts."""
import base64
import configparser
import hashlib
import ipaddress
import re


def parse_networks(value):
    try:
        networks=[ipaddress.IPv4Network(n.strip()) for n in value.split(',')]
        private=[ipaddress.IPv4Network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')]
        tunnel=ipaddress.IPv4Network('10.2.0.0/24')
        if not 1<=len(networks)<=8 or any(not any(n.subnet_of(p) for p in private) or n.overlaps(tunnel) for n in networks):
            raise ValueError()
        return networks
    except (ValueError,AttributeError):
        raise ValueError('LAN_SUBNETS skal indeholde private IPv4-net i CIDR-format uden overlap med 10.2.0.0/24.') from None


def parse_wireguard(raw):
    if not raw or len(raw) > 16384:
        raise ValueError('Vælg en WireGuard .conf-fil på højst 16 KB.')
    try:
        text = raw.decode('utf-8-sig')
        parser = configparser.ConfigParser(interpolation=None, strict=True,
                                           inline_comment_prefixes=('#', ';'))
        parser.optionxform = str
        parser.read_string(text)
        if parser.defaults() or set(parser.sections()) != {'Interface', 'Peer'}:
            raise ValueError()
        interface, peer = dict(parser['Interface']), dict(parser['Peer'])
        if set(interface) - {'PrivateKey', 'Address', 'DNS', 'MTU'}:
            raise ValueError()
        if set(peer) - {'PublicKey', 'PresharedKey', 'AllowedIPs', 'Endpoint', 'PersistentKeepalive'}:
            raise ValueError()
        for key in (interface['PrivateKey'], peer['PublicKey']):
            if len(base64.b64decode(key, validate=True)) != 32:
                raise ValueError()
        if 'PresharedKey' in peer and len(base64.b64decode(peer['PresharedKey'], validate=True)) != 32:
            raise ValueError()
        addresses = [ipaddress.ip_interface(s.strip()) for s in interface['Address'].split(',')]
        v4 = [str(a) for a in addresses if a.version == 4]
        if len(v4) != 1:
            raise ValueError()
        allowed = [ipaddress.ip_network(s.strip()) for s in peer['AllowedIPs'].split(',')]
        if ipaddress.ip_network('0.0.0.0/0') not in allowed:
            raise ValueError()
        endpoint, port = peer['Endpoint'].rsplit(':', 1)
        address = ipaddress.IPv4Address(endpoint)
        if not address.is_global or not 1 <= int(port) <= 65535:
            raise ValueError()
        if 'PersistentKeepalive' in peer and not 0 <= int(peer['PersistentKeepalive']) <= 65535:
            raise ValueError()
        if 'MTU' in interface and not 1280 <= int(interface['MTU']) <= 1500:
            raise ValueError()
        dns = [str(ipaddress.IPv4Address(s.strip())) for s in interface.get('DNS', '10.2.0.1').split(',')]
    except (ValueError, KeyError, configparser.Error, UnicodeError):
        raise ValueError('Filen skal være en Proton WireGuard-konfiguration med én IPv4-server. Scripts og ekstra sektioner accepteres ikke.') from None
    # IPv6 is deliberately excluded: each tunnel has an IPv4-only namespace.
    interface['Address'] = ', '.join(v4)
    interface['DNS'] = ', '.join(dns)
    peer['AllowedIPs'] = '0.0.0.0/0'
    normalized = '[Interface]\n' + ''.join(f'{k} = {v}\n' for k, v in interface.items())
    normalized += '\n[Peer]\n' + ''.join(f'{k} = {v}\n' for k, v in peer.items())
    return normalized, {
        'endpoint': f'{address}:{int(port)}',
        'fingerprint': hashlib.sha256(base64.b64decode(interface['PrivateKey'])).hexdigest(),
        'ipv6_removed': any(a.version == 6 for a in addresses),
    }


def validate_target(data, networks):
    name = data.get('name', '')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
        raise ValueError('Navnet skal være mellem 1 og 64 tegn.')
    enabled = data.get('relay_enabled', False)
    if type(enabled) is not bool:
        raise ValueError('Ugyldigt valg af videresendelse.')
    host = data.get('host', '')
    port = data.get('port')
    if not isinstance(host, str):
        raise ValueError('Indtast en lokal IPv4-adresse.')
    host = host.strip()
    if host:
        try:
            ip = ipaddress.IPv4Address(host)
        except ValueError:
            raise ValueError('Indtast en lokal IPv4-adresse.') from None
        if not any(ip in n and ip not in (n.network_address, n.broadcast_address) for n in networks):
            raise ValueError('Målet skal ligge i det tilladte lokalnet.')
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise ValueError('Port skal være mellem 1 og 65535.')
    if enabled and (not host or port is None):
        raise ValueError('Vælg lokal IP og port for videresendelsen.')
    return {'name': name.strip(), 'host': host, 'port': port, 'relay_enabled': enabled}
