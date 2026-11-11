"""Only the synchronous provider transport may dial the registered host and port."""
from contextlib import contextmanager
import ipaddress
import socket
import threading
from urllib.parse import urlsplit


SERVICE_MODES = {"loopback_http", "deepseek"}
_permit = threading.local()


def endpoint(config):
    if config["transport"] == "deepseek":
        if config.get("loopback_url") is not None:
            raise ValueError("A real teacher uses only its registered HTTPS endpoint")
        from harness_deepseek_adapter import BASE_URL
        return BASE_URL
    if config["transport"] != "loopback_http":
        raise ValueError("Unknown teacher service transport")
    url = config["loopback_url"]
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or
            url != f"http://127.0.0.1:{parsed.port}"):
        raise ValueError("Local teacher requires an explicit IPv4 loopback port")
    return url


def restrict_network(base):
    parsed = urlsplit(base)
    host, port = parsed.hostname, parsed.port or 443
    attempts = []
    resolve, connect, connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex

    def reject():
        attempts.append("forbidden_provider_socket")
        raise PermissionError("Only the registered teacher transport may connect")

    def getaddrinfo(name, service_port, *args, **kwargs):
        name = name.decode("ascii") if isinstance(name, bytes) else name
        if getattr(_permit, "base", None) != base or (name, service_port) != (host, port):
            reject()
        addresses = resolve(name, service_port, *args, **kwargs)
        _permit.addresses.update(row[-1][0] for row in addresses)
        return addresses

    def check(sock, addr):
        if (getattr(_permit, "base", None) != base or sock.family not in (socket.AF_INET, socket.AF_INET6) or
                not isinstance(addr, tuple) or addr[1] != port or addr[0] not in _permit.addresses):
            reject()

    def dial(sock, addr):
        check(sock, addr)
        return connect(sock, addr)

    def dial_ex(sock, addr):
        check(sock, addr)
        return connect_ex(sock, addr)

    socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = getaddrinfo, dial, dial_ex
    return attempts


@contextmanager
def allow_registered_transport(base):
    if getattr(_permit, "base", None) is not None:
        raise RuntimeError("Nested network grant")
    _permit.base = base
    host = urlsplit(base).hostname
    try:
        _permit.addresses = {str(ipaddress.ip_address(host))}
    except ValueError:
        _permit.addresses = set()
    try:
        yield
    finally:
        del _permit.base, _permit.addresses
