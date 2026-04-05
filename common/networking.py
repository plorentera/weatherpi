from __future__ import annotations

import socket


def detect_primary_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            candidate = sock.getsockname()[0]
            if candidate and not candidate.startswith("127."):
                return candidate
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM):
            candidate = str(sockaddr[0] or "").strip()
            if candidate and not candidate.startswith("127."):
                return candidate
    except Exception:
        pass

    return ""
