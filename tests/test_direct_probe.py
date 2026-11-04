"""物理接口探测：DNS 不能接受伪造响应或非公网结果。"""
import importlib.util
import socket
import struct

import pytest


def test_direct_probe_module_exists():
    assert importlib.util.find_spec('iptv_manager.direct_probe') is not None


def _api():
    from iptv_manager.direct_probe import parse_dns_response, dns_query, DirectConfig
    return parse_dns_response, dns_query, DirectConfig


def _reply(ip='36.155.131.35', transaction_id=123):
    _, query, _ = _api()
    question = query('ali-m-l.cztv.com', 123)[12:]
    return struct.pack('!6H', transaction_id, 0x8180, 1, 1, 0, 0) + question + b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 60, 4) + socket.inet_aton(ip)


def test_dns_accepts_only_matching_public_a():
    parse, _, _ = _api()
    assert parse(_reply(), 123, 'ali-m-l.cztv.com') == (['36.155.131.35'], [])


@pytest.mark.parametrize('ip', ['127.0.0.1', '192.168.31.120', '198.18.0.1', '224.0.0.1', '0.0.0.0'])
def test_dns_rejects_nonpublic_including_fakeip(ip):
    parse, _, _ = _api()
    with pytest.raises(ValueError):
        parse(_reply(ip), 123, 'ali-m-l.cztv.com')


def test_dns_rejects_wrong_id_and_truncated_records():
    parse, _, _ = _api()
    for packet in [_reply(transaction_id=124), _reply()[:-1], b'', _reply()[:12]]:
        with pytest.raises(ValueError):
            parse(packet, 123, 'ali-m-l.cztv.com')


def test_dns_handles_cname_and_rejects_pointer_loop():
    parse, query, _ = _api()
    question = query('ali-m-l.cztv.com', 123)[12:]
    alias = b'\x03cdn\x04cztv\x03com\x00'
    cname = b'\xc0\x0c' + struct.pack('!HHIH', 5, 1, 60, len(alias)) + alias
    record = alias + struct.pack('!HHIH', 1, 1, 60, 4) + socket.inet_aton('36.155.131.35')
    packet = struct.pack('!6H', 123, 0x8180, 1, 2, 0, 0) + question + cname + record
    assert parse(packet, 123, 'ali-m-l.cztv.com') == (['36.155.131.35'], ['cdn.cztv.com'])
    with pytest.raises(ValueError):
        parse(packet[:12] + b'\xc0\x0c' + packet[14:], 123, 'ali-m-l.cztv.com')


def test_configuration_requires_explicit_physical_ipv4_and_interface():
    _, _, config = _api()
    with pytest.raises(ValueError):
        config(interface_index=0, local_ipv4='192.168.31.120', dns_server='192.168.31.1')
    with pytest.raises(ValueError):
        config(interface_index=4, local_ipv4='::1', dns_server='192.168.31.1')


def test_http_connection_close_body_remains_readable(monkeypatch):
    """HTTPConnection.getresponse 可关闭原 socket，但 response 的文件仍然可读。"""
    from iptv_manager.direct_probe import DirectConfig, DirectTransport
    import httpx
    class Sock:
        closed = False
        def connect(self, address): pass
        def close(self): self.closed = True
        def settimeout(self, value):
            if self.closed:
                raise OSError('socket already closed by getresponse')
    sock = Sock()
    class Response:
        status = 200
        chunks = [b'#EXTM3U', b'']
        def read1(self, count): return self.chunks.pop(0)
        def getheaders(self): return [('Content-Type', 'text/plain')]
        def close(self): pass
    class Connection:
        def __init__(self, *args, **kwargs): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self):
            sock.close()
            return Response()
        def close(self): pass
    transport = DirectTransport(DirectConfig(4, '192.168.31.120', '192.168.31.1'))
    monkeypatch.setattr(transport.resolver, 'socket', lambda: sock)
    monkeypatch.setattr('iptv_manager.direct_probe.http.client.HTTPConnection', Connection)
    assert transport._request(httpx.Request('GET', 'http://36.155.131.35/live.m3u8')).content == b'#EXTM3U'


def test_physical_socket_sets_network_order_interface_and_local_binding(monkeypatch):
    from iptv_manager.direct_probe import DirectConfig, PhysicalResolver
    calls = []
    class Sock:
        def setsockopt(self, *args): calls.append(('interface', args))
        def bind(self, value): calls.append(('bind', value))
        def settimeout(self, value): calls.append(('timeout', value))
    sock = Sock()
    monkeypatch.setattr('iptv_manager.direct_probe.sys.platform', 'win32')
    monkeypatch.setattr('iptv_manager.direct_probe.socket.socket', lambda *a: sock)
    resolver = PhysicalResolver(DirectConfig(4, '192.168.31.120', '192.168.31.1'))
    assert resolver.socket(socket.SOCK_DGRAM) is sock
    assert calls[:2] == [('interface', (socket.IPPROTO_IP, 31, b'\0\0\0\4')), ('bind', ('192.168.31.120', 0))]


@pytest.mark.asyncio
async def test_redirect_to_private_url_is_rejected_before_any_socket():
    import httpx
    from iptv_manager.direct_probe import DirectConfig, DirectTransport
    transport = DirectTransport(DirectConfig(4, '192.168.31.120', '192.168.31.1'))
    with pytest.raises(ValueError, match='UNSAFE_DIRECT_REQUEST'):
        await transport.handle_async_request(httpx.Request('GET', 'http://127.0.0.1/live.m3u8'))


def test_dns_rejects_wrong_question_and_truncated_cname():
    parse, _, _ = _api()
    with pytest.raises(ValueError):
        parse(_reply(), 123, 'wrong.example.com')
    packet = bytearray(_reply())
    packet[-14:-12] = struct.pack('!H', 5)
    with pytest.raises(ValueError):
        parse(bytes(packet), 123, 'ali-m-l.cztv.com')


def test_live_progress_rejects_frozen_playlist_even_when_decodable():
    from iptv_manager.direct_probe import verify_live_progress
    previous = '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:10\n#EXTINF:8\na.ts\n#EXTINF:8\nb.ts\n'
    with pytest.raises(ValueError, match='FROZEN_HLS_PLAYLIST'):
        verify_live_progress(previous, previous)
    advanced = '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:11\n#EXTINF:8\nb.ts\n#EXTINF:8\nc.ts\n'
    verify_live_progress(previous, advanced)


def test_live_progress_rejects_old_program_time():
    from datetime import datetime, UTC
    from iptv_manager.direct_probe import verify_live_progress
    previous = '#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:2023-09-11T12:49:33+0800\na.ts\n'
    with pytest.raises(ValueError, match='STALE_HLS_PROGRAM_TIME'):
        verify_live_progress(previous, previous.replace('a.ts', 'b.ts'), now=datetime(2026, 11, 4, tzinfo=UTC))
