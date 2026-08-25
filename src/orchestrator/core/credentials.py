"""Secure credential resolution.

Order of precedence, highest first:

1. Process environment (``ORC_*``) — CI friendly.
2. The OS keychain via ``keyring`` (macOS Keychain, libsecret, Windows Credential
   Manager) — the recommended place for a developer laptop.
3. A ``.env`` file in the global config directory, readable only by the user.

Secrets are never written to the config YAML, never echoed, and never included
in audit events or engine prompts. ``redact`` is used everywhere a value could
plausibly reach a log line.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from orchestrator.core.errors import CredentialError

KEYRING_SERVICE = "mobile-eng-orchestrator"
ENV_PREFIX = "ORC_"


def redact(value: str | None, *, keep: int = 4) -> str:
    """Render a secret safe for display: ``ATATT…9f2c`` (never the middle)."""
    if not value:
        return "(unset)"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class CredentialSource:
    name: str
    detail: str = ""


class CredentialStore:
    """Reads secrets from env / keychain / dotenv without ever persisting them in YAML."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.env_file = config_dir / ".env"

    # -- reading ------------------------------------------------------------ #

    def get(self, key: str) -> tuple[str | None, CredentialSource]:
        env_key = key if key.startswith(ENV_PREFIX) else f"{ENV_PREFIX}{key}"
        if value := os.environ.get(env_key):
            return value, CredentialSource("environment", env_key)
        if value := self._keyring_get(env_key):
            return value, CredentialSource("keychain", KEYRING_SERVICE)
        if value := _load_env_file(self.env_file).get(env_key):
            return value, CredentialSource("dotenv", str(self.env_file))
        return None, CredentialSource("missing")

    def require(self, key: str, *, purpose: str) -> str:
        value, _ = self.get(key)
        if not value:
            env_key = key if key.startswith(ENV_PREFIX) else f"{ENV_PREFIX}{key}"
            raise CredentialError(
                f"Missing credential {env_key} (needed for {purpose}).",
                hint=(
                    f"Set it with `orc config set-secret {env_key}` (stored in your OS keychain) "
                    f"or export {env_key} in your shell."
                ),
            )
        return value

    # -- writing ------------------------------------------------------------ #

    def set(self, key: str, value: str, *, prefer_keyring: bool = True) -> CredentialSource:
        env_key = key if key.startswith(ENV_PREFIX) else f"{ENV_PREFIX}{key}"
        if prefer_keyring and self._keyring_set(env_key, value):
            return CredentialSource("keychain", KEYRING_SERVICE)
        self._dotenv_set(env_key, value)
        return CredentialSource("dotenv", str(self.env_file))

    def delete(self, key: str) -> None:
        env_key = key if key.startswith(ENV_PREFIX) else f"{ENV_PREFIX}{key}"
        self._keyring_delete(env_key)
        values = _load_env_file(self.env_file)
        if env_key in values:
            values.pop(env_key)
            self._write_dotenv(values)

    # -- backends ------------------------------------------------------------ #

    @staticmethod
    def _keyring() -> object | None:
        try:
            import keyring  # type: ignore[import-not-found]
        except Exception:  # pragma: no cover - optional dependency
            return None
        return keyring

    def _keyring_get(self, key: str) -> str | None:
        backend = self._keyring()
        if backend is None:
            return None
        try:
            return backend.get_password(KEYRING_SERVICE, key)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - locked/unavailable keychain
            return None

    def _keyring_set(self, key: str, value: str) -> bool:
        backend = self._keyring()
        if backend is None:
            return False
        try:
            backend.set_password(KEYRING_SERVICE, key, value)  # type: ignore[attr-defined]
            return True
        except Exception:  # pragma: no cover
            return False

    def _keyring_delete(self, key: str) -> None:
        backend = self._keyring()
        if backend is None:
            return
        try:
            backend.delete_password(KEYRING_SERVICE, key)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass

    def _dotenv_set(self, key: str, value: str) -> None:
        values = _load_env_file(self.env_file)
        values[key] = value
        self._write_dotenv(values)

    def _write_dotenv(self, values: dict[str, str]) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        body = "\n".join(f"{k}={v}" for k, v in sorted(values.items())) + "\n"
        self.env_file.write_text(body, encoding="utf-8")
        try:
            self.env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:  # pragma: no cover - non-POSIX
            pass
