"""Windows 物理网卡直连探测；DNS、TCP、TLS 均绑定指定接口，不修改系统路由。"""
from __future__ import annotations

import asyncio
import http.client
import ipaddress
import secrets
import socket
import ssl
import struct
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .tv_trial import safe_url
from .hls_freshness import verify_live_progress


def _public_ipv4(value: str) -> str:
    address = ipaddress.IPv4Address(value)
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise ValueError('NONPUBLIC_IPV4')
    return str(address)


@dataclass(frozen=True)
class DirectConfig:
    interface_index: int
    local_ipv4: str
    dns_server: str
    timeout: float = 8.0
    max_response_bytes: int = 16_000_001

    def __post_init__(self):
        if self.interface_index <= 0 or self.timeout <= 0 or self.max_response_bytes <= 0:
            raise ValueError('INVALID_DIRECT_CONFIG')
        ipaddress.IPv4Address(self.local_ipv4)
        ipaddress.IPv4Address(self.dns_server)


def dns_query(host: str, transaction_id: int) -> bytes:
    labels = host.rstrip('.').encode('idna').split(b'.')
    if any(not part or len(part) > 63 for part in labels) or sum(map(len, labels)) + len(labels) > 254:
        raise ValueError('INVALID_DNS_NAME')
    return struct.pack('!6H', transaction_id, 0x0100, 1, 0, 0, 0) + b''.join(bytes([len(p)]) + p for p in labels) + b'\0\0\1\0\1'


def _name(packet: bytes, offset: int) -> tuple[str, int]:
    labels, visited = [], set()
    end = None
    while True:
        if offset >= len(packet) or offset in visited or len(visited) > 128:
            raise ValueError('INVALID_DNS_NAME')
        visited.add(offset)
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError('TRUNCATED_DNS_POINTER')
            end = end if end is not None else offset + 2
            offset = ((length & 0x3F) << 8) | packet[offset + 1]
            continue
        if length & 0xC0 or offset + 1 + length > len(packet):
            raise ValueError('TRUNCATED_DNS_LABEL')
        offset += 1
        if not length:
            return '.'.join(labels).lower(), end if end is not None else offset
        try:
            labels.append(packet[offset:offset + length].decode('ascii'))
        except UnicodeDecodeError as exc:
            raise ValueError('INVALID_DNS_LABEL') from exc
        if sum(map(len, labels)) + len(labels) > 254:
            raise ValueError('DNS_NAME_TOO_LONG')
        offset += length


def parse_dns_response(packet: bytes, transaction_id: int, host: str) -> tuple[list[str], list[str]]:
    if len(packet) < 12:
        raise ValueError('TRUNCATED_DNS_HEADER')
    ident, flags, qd, an, ns, ar = struct.unpack('!6H', packet[:12])
    if ident != transaction_id or not flags & 0x8000 or flags & 0x7A0F or qd != 1:
        raise ValueError('INVALID_DNS_RESPONSE')
    question, offset = _name(packet, 12)
    if question != host.rstrip('.').lower() or packet[offset:offset + 4] != b'\0\1\0\1':
        raise ValueError('DNS_QUESTION_MISMATCH')
    offset += 4
    aliases, addresses = {}, {}
    for index in range(an + ns + ar):
        owner, offset = _name(packet, offset)
        if offset + 10 > len(packet):
            raise ValueError('TRUNCATED_DNS_RECORD')
        kind, cls, ttl, size = struct.unpack('!HHIH', packet[offset:offset + 10])
        offset += 10
        if offset + size > len(packet):
            raise ValueError('TRUNCATED_DNS_DATA')
        if index < an and cls == 1:
            if kind == 1:
                if size != 4:
                    raise ValueError('INVALID_DNS_A')
                addresses.setdefault(owner, []).append(socket.inet_ntoa(packet[offset:offset + 4]))
            elif kind == 5:
                alias, end = _name(packet, offset)
                if end != offset + size:
                    raise ValueError('INVALID_DNS_CNAME')
                aliases[owner] = alias
        offset += size
    chain, current = [], question
    while current in aliases:
        current = aliases[current]
        if current == question or current in chain or len(chain) >= 8:
            raise ValueError('DNS_CNAME_LOOP')
        chain.append(current)
    ips = [_public_ipv4(ip) for ip in addresses.get(current, [])]
    return list(dict.fromkeys(ips)), chain


class PhysicalResolver:
    def __init__(self, config: DirectConfig):
        self.config = config
        self.cache: dict[str, list[str]] = {}
        self.observations: list[dict] = []

    def socket(self, kind=socket.SOCK_STREAM) -> socket.socket:
        if sys.platform != 'win32':
            raise OSError('WINDOWS_PHYSICAL_INTERFACE_REQUIRED')
        sock = socket.socket(socket.AF_INET, kind)
        try:
            # Windows 的 IP_UNICAST_IF 要求接口编号使用网络字节序。
            sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack('!I', self.config.interface_index))
            sock.bind((self.config.local_ipv4, 0))
            sock.settimeout(self.config.timeout)
            return sock
        except BaseException:
            sock.close()
            raise

    def resolve(self, host: str) -> list[str]:
        try:
            return [_public_ipv4(host)]
        except ipaddress.AddressValueError:
            pass
        if host in self.cache:
            return self.cache[host]
        current = host
        for _ in range(8):
            ident = secrets.randbelow(65536)
            with self.socket(socket.SOCK_DGRAM) as sock:
                sock.connect((self.config.dns_server, 53))
                sock.send(dns_query(current, ident))
                packet = sock.recv(65535)
            ips, aliases = parse_dns_response(packet, ident, current)
            if ips:
                self.cache[host] = ips
                self.observations.append({'host': host, 'ipv4': ips, 'dns_server': self.config.dns_server,
                                          'interface_index': self.config.interface_index})
                return ips
            if not aliases:
                break
            current = aliases[-1]
        raise ValueError('NO_PUBLIC_DNS_A')

    async def guard(self, request: httpx.Request) -> None:
        if not safe_url(str(request.url)):
            raise ValueError('UNSAFE_URL')
        await asyncio.to_thread(self.resolve, request.url.host)


class DirectTransport(httpx.AsyncBaseTransport):
    """HTTPX 适配器；重定向的新请求仍经同一绑定网卡，HTTPS 保留原域名 SNI 和证书校验。"""
    def __init__(self, config: DirectConfig):
        self.config = config
        self.resolver = PhysicalResolver(config)
        self.requests: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method not in {'GET', 'HEAD'} or not safe_url(str(request.url)):
            raise ValueError('UNSAFE_DIRECT_REQUEST')
        return await asyncio.to_thread(self._request, request)

    def _request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        ips = self.resolver.resolve(host)
        last_error = None
        started = time.monotonic()
        for ip in ips:
            conn = http.client.HTTPConnection(host, request.url.port, timeout=self.config.timeout)
            sock = None
            response = None
            try:
                sock = self.resolver.socket()
                sock.connect((ip, request.url.port or (443 if request.url.scheme == 'https' else 80)))
                if request.url.scheme == 'https':
                    sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
                conn.sock = sock
                headers = dict(request.headers)
                headers['Accept-Encoding'] = 'identity'
                headers['Connection'] = 'close'
                conn.request(request.method, request.url.raw_path.decode('ascii'), headers=headers)
                response = conn.getresponse()
                chunks, count = [], 0
                while count < self.config.max_response_bytes and request.method != 'HEAD':
                    remaining = self.config.timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError('DIRECT_RESPONSE_DEADLINE')
                    chunk = response.read1(min(65536, self.config.max_response_bytes - count))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    count += len(chunk)
                self.requests.append({'host': host, 'peer_ipv4': ip, 'interface_index': self.config.interface_index,
                                      'status': response.status, 'bytes': count, 'seconds': round(time.monotonic() - started, 3)})
                return httpx.Response(response.status, headers=response.getheaders(), content=b''.join(chunks), request=request)
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
            finally:
                if response is not None:
                    response.close()
                conn.close()
                if sock is not None:
                    sock.close()
        raise httpx.ConnectError(type(last_error).__name__ if last_error else 'DIRECT_FAILED', request=request) from last_error


def direct_client(config: DirectConfig) -> httpx.AsyncClient:
    transport = DirectTransport(config)
    return httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=True, max_redirects=5,
                             event_hooks={'request': [transport.resolver.guard]})


