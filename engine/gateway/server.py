from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

import uvicorn

from engine.runtime.ports import find_free_port

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


class GatewayServer:
    """Runs the gateway ASGI app in a background thread using Uvicorn."""

    def __init__(
        self,
        app: FastAPI,
        host: str = "127.0.0.1",
        port: int = 8001,
    ) -> None:
        self.app = app
        self.host = host
        self.requested_port = port
        self.actual_port: int | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self.actual_port is not None:
            return self.actual_port
        return self.requested_port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def probe_token(self) -> str | None:
        return getattr(self.app.state, "probe_token", None)

    def start(self, timeout_s: float = 5.0) -> None:
        """Start the gateway server in a background thread and wait until ready."""
        port = self.requested_port
        if port == 0:
            port = find_free_port()
        self.actual_port = port

        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.actual_port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)

        self._thread = threading.Thread(
            target=self._server.run,
            name="gateway-server",
            daemon=True,
        )
        self._thread.start()

        # Wait until server is listening
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._server.started:
                # If bound to dynamic port, extract exact port
                if self._server.servers:
                    for s in self._server.servers:
                        for sock in getattr(s, "sockets", ()):
                            sockname = sock.getsockname()
                            if sockname and len(sockname) >= 2:
                                self.actual_port = sockname[1]
                                break
                logger.info("Gateway server listening at %s", self.url)
                return
            time.sleep(0.02)

        raise TimeoutError(f"Gateway server failed to start within {timeout_s}s")

    def stop(self, timeout_s: float = 5.0) -> None:
        """Signal the server to stop and join its thread."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout_s)
            self._thread = None
        self._server = None
