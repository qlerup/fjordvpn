"""Bounded Minecraft Java status checks; no login or game-world mutation."""
import json
import socket
import struct

from stream_sync import StreamPending


def varint(number):
    number &= 0xffffffff
    result=bytearray()
    while True:
        byte=number & 127;number >>= 7
        result.append(byte | 128 if number else byte)
        if not number:
            return bytes(result)


def read_exact(connection,size):
    result=b''
    while len(result)<size:
        part=connection.recv(size-len(result))
        if not part:
            raise ValueError('Minecraft-serveren lukkede forbindelsen.')
        result+=part
    return result


def read_varint(connection):
    value=0
    for shift in range(0,35,7):
        byte=read_exact(connection,1)[0];value|=(byte & 127)<<shift
        if not byte & 128:
            return value
    raise ValueError('Ugyldigt svar fra Minecraft-serveren.')


def probe(host,port,domain):
    with socket.create_connection((host,port),timeout=5) as connection:
        encoded=domain.encode('utf-8')
        packet=b'\x00'+varint(47)+varint(len(encoded))+encoded+struct.pack('>H',port)+b'\x01'
        connection.sendall(varint(len(packet))+packet+b'\x01\x00')
        size=read_varint(connection)
        if not 2<=size<=65536 or read_varint(connection)!=0:
            raise ValueError('Ugyldigt Minecraft-statussvar.')
        length=read_varint(connection)
        if not 1<=length<=size:
            raise ValueError('Ugyldigt Minecraft-statussvar.')
        result=json.loads(read_exact(connection,length))
        if not isinstance(result,dict) or not isinstance(result.get('version'),dict):
            raise ValueError('Forbindelsen svarer ikke som en Minecraft Java-server.')
        return result['version']


def public_probe(domain,port,address):
    try:
        addresses={r[4][0] for r in socket.getaddrinfo(domain,port,socket.AF_INET,socket.SOCK_STREAM)}
        if address not in addresses:
            raise StreamPending('Afventer Minecraft-domænets nye DNS-adresse.')
        return probe(domain,port,domain)
    except (OSError,ValueError) as exc:
        if isinstance(exc,StreamPending):
            raise
        raise StreamPending('Afventer en fungerende Minecraft Java-forbindelse.') from exc
