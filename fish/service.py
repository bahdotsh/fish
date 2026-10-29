"""Starts the SDK's service (``offline-protocol-service``) for one Fish
player, with its own home directory, config, keys and socket.

A player home (default ``~/.fish/<name>``) holds config.json, store.key
(0600, made once), keys/ (the sealed MLS store), state/ and service.log.

The socket lives at ``/tmp/fish-<uid>/<hash>.sock``, because a Unix socket
path is limited to about 100 bytes and a deep home would not fit.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_ID = "fish"

BASE_CONFIG = {
    "app_id": APP_ID,
    "profile": "device",
    "internet_enabled": False,
    "reticulum_enabled": False,
    "nostr_enabled": False,
    "prefer_online": False,
    "initial_ttl": 5,
    "encryption_enabled": True,
    "require_encryption": True,
    "auto_key_exchange": True,
    "store_pending": True,
    "max_pending_per_peer": 200,
    "max_pending_global": 2000,
    "pending_ttl_ms": 3600000,
    "overflow_policy": "DropOldest",
}

LOG_ROTATE_BYTES = 10 * 1024 * 1024

# "lan" suits a room on one Wi-Fi network and rehearsals on one machine.
TRANSPORTS = {
    "ble": {"ble_enabled": True, "wifi_direct_enabled": False},
    "lan": {"ble_enabled": False, "wifi_direct_enabled": True},
    "both": {"ble_enabled": True, "wifi_direct_enabled": True},
}


def default_home(name: str) -> Path:
    return Path(os.environ.get("FISH_HOME", Path.home() / ".fish")) / name.lower()


@dataclass
class ServiceSpec:
    home: Path
    transport: str = "lan"
    listen: str | None = None
    peers: list[str] = field(default_factory=list)
    lan_discovery: bool = False
    log_level: str = os.environ.get("FISH_SERVICE_LOG", "INFO")

    @property
    def socket_path(self) -> Path:
        digest = hashlib.sha256(str(self.home.resolve()).encode()).hexdigest()[:12]
        return Path(f"/tmp/fish-{os.getuid()}") / f"{digest}.sock"

    def prepare(self) -> dict[str, str]:
        """Creates the home, writes the config, returns the environment."""
        if self.transport not in TRANSPORTS:
            raise ValueError(f"transport must be one of {', '.join(TRANSPORTS)}")
        self.home.mkdir(parents=True, exist_ok=True)
        for sub in ("keys", "state"):
            (self.home / sub).mkdir(exist_ok=True)
        run = self.socket_path.parent
        run.mkdir(exist_ok=True)
        os.chmod(run, 0o700)
        config = {**BASE_CONFIG, **TRANSPORTS[self.transport]}
        (self.home / "config.json").write_text(json.dumps(config, indent=2) + "\n")
        key_file = self.home / "store.key"
        if not key_file.exists():
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(secrets.token_hex(32))
        env = dict(os.environ)
        env["OFFLINE_PROTOCOL_STORE_KEY"] = key_file.read_text().strip()
        return env

    def command(self) -> list[str]:
        exe = shutil.which("offline-protocol-service", path=str(Path(sys.executable).parent)) or shutil.which(
            "offline-protocol-service"
        )
        if exe is None:
            raise FileNotFoundError("offline-protocol-service not found; pip install offline-protocol-sdk")
        cmd = [
            exe,
            "--config", str(self.home / "config.json"),
            "--socket", str(self.socket_path),
            "--mls-root", str(self.home / "keys"),
            "--state-root", str(self.home / "state"),
            "--log-level", self.log_level,
        ]
        if self.transport in ("lan", "both"):
            if self.listen:
                cmd += ["--listen", self.listen]
            for peer in self.peers:
                cmd += ["--peer", peer]
            if self.lan_discovery:
                cmd.append("--lan")
        return cmd

    def spawn(self, log_to_file: bool = True) -> subprocess.Popen[bytes]:
        env = self.prepare()
        log = self.home / "service.log"
        if log_to_file and log.exists() and log.stat().st_size > LOG_ROTATE_BYTES:
            log.replace(self.home / "service.log.1")
        stdout = open(log, "ab") if log_to_file else None
        return subprocess.Popen(
            self.command(), env=env, stdout=stdout, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
        )
