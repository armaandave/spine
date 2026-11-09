"""Tests never reach the internet.

A provider call a test didn't mock fails at once, naming the host, instead of waiting on a
real service: a slow provider used to stall the suite until CI's time limit.
"""

import socket

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class OutsideConnectionBlockedError(ConnectionRefusedError):
    """A test tried to reach a host off this machine."""


def _is_local(host):
    host = host.decode() if isinstance(host, bytes) else str(host)
    return host in LOCAL_HOSTS or host.startswith("127.")


def block_outside_connections():
    """Refuse every connection off this machine, in this process and its children."""
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def refuse(host):
        message = f"Tests can't reach {host}: mock the call to it."
        raise OutsideConnectionBlockedError(message)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not _is_local(host):
            refuse(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(address[0]):
            refuse(address[0])
        return real_connect(self, address)

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect
