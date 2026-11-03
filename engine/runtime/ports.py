import socket

HOST = "127.0.0.1"


def find_free_port() -> int:

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]

def port_is_free(port: int) -> bool:
    """Same bind semantics servers use (SO_REUSEADDR), so TIME_WAIT doesn't count as taken."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
        return True