from __future__ import annotations

from typing import Protocol


SERVICE_NAME = "ClipRadar"
GEMINI_KEY_NAME = "gemini_api_key"


class SecretStore(Protocol):
    def get_gemini_key(self) -> str | None: ...
    def set_gemini_key(self, value: str) -> None: ...
    def delete_gemini_key(self) -> None: ...


class KeyringSecretStore:
    """Stores the key in the OS credential vault, never in SQLite or app files."""

    def get_gemini_key(self) -> str | None:
        import keyring

        try:
            return keyring.get_password(SERVICE_NAME, GEMINI_KEY_NAME)
        except Exception as exc:
            raise RuntimeError("The operating-system credential vault is unavailable.") from exc

    def set_gemini_key(self, value: str) -> None:
        import keyring

        cleaned = value.strip()
        if not cleaned:
            self.delete_gemini_key()
            return
        try:
            keyring.set_password(SERVICE_NAME, GEMINI_KEY_NAME, cleaned)
        except Exception as exc:
            raise RuntimeError("The API key could not be saved in the operating-system credential vault.") from exc

    def delete_gemini_key(self) -> None:
        import keyring

        try:
            keyring.delete_password(SERVICE_NAME, GEMINI_KEY_NAME)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception as exc:
            raise RuntimeError("The stored API key could not be removed from the credential vault.") from exc


class MemorySecretStore:
    """Test-only secret store. It intentionally has no file persistence."""

    def __init__(self, value: str | None = None):
        self.value = value

    def get_gemini_key(self) -> str | None:
        return self.value

    def set_gemini_key(self, value: str) -> None:
        self.value = value.strip() or None

    def delete_gemini_key(self) -> None:
        self.value = None

