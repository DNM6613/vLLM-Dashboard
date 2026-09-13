import socket


class RealNetworkAccessError(RuntimeError):
    pass

def _blocked_create_connection(*_args, **_kwargs):
    raise RealNetworkAccessError(
        "socket.create_connection is forbidden in tests — mock the "
        "network boundary (see tests/__init__.py guard)")

socket.create_connection = _blocked_create_connection
