from __future__ import annotations

from typing import Protocol


SERVICE_NAME = "ClipRadar"
GEMINI_KEY_NAME = "gemini_api_key"
YOUTUBE_CLIENT_NAME = "youtube_oauth_client"
YOUTUBE_TOKEN_PREFIX = "youtube_oauth_token:"
YOUTUBE_SESSION_PREFIX = "youtube_upload_session:"


class SecretStore(Protocol):
    def get_gemini_key(self) -> str | None: ...
    def set_gemini_key(self, value: str) -> None: ...
    def delete_gemini_key(self) -> None: ...
    def get_youtube_client(self) -> str | None: ...
    def set_youtube_client(self, value: str) -> None: ...
    def get_youtube_token(self, credential_key: str) -> str | None: ...
    def set_youtube_token(self, credential_key: str, value: str) -> None: ...
    def delete_youtube_token(self, credential_key: str) -> None: ...
    def get_youtube_upload_session(self, job_id: str) -> str | None: ...
    def set_youtube_upload_session(self, job_id: str, value: str) -> None: ...
    def delete_youtube_upload_session(self, job_id: str) -> None: ...


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

    def _get(self, name: str) -> str | None:
        import keyring

        try:
            return keyring.get_password(SERVICE_NAME, name)
        except Exception as exc:
            raise RuntimeError("The operating-system credential vault is unavailable.") from exc

    def _set(self, name: str, value: str) -> None:
        import keyring

        try:
            keyring.set_password(SERVICE_NAME, name, value)
        except Exception as exc:
            raise RuntimeError("The credential could not be saved in the operating-system credential vault.") from exc

    def _delete(self, name: str) -> None:
        import keyring

        try:
            keyring.delete_password(SERVICE_NAME, name)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception as exc:
            raise RuntimeError("The stored credential could not be removed from the credential vault.") from exc

    def get_youtube_client(self) -> str | None:
        return self._get(YOUTUBE_CLIENT_NAME)

    def set_youtube_client(self, value: str) -> None:
        self._set(YOUTUBE_CLIENT_NAME, value)

    def get_youtube_token(self, credential_key: str) -> str | None:
        return self._get(f"{YOUTUBE_TOKEN_PREFIX}{credential_key}")

    def set_youtube_token(self, credential_key: str, value: str) -> None:
        self._set(f"{YOUTUBE_TOKEN_PREFIX}{credential_key}", value)

    def delete_youtube_token(self, credential_key: str) -> None:
        self._delete(f"{YOUTUBE_TOKEN_PREFIX}{credential_key}")

    def get_youtube_upload_session(self, job_id: str) -> str | None:
        return self._get(f"{YOUTUBE_SESSION_PREFIX}{job_id}")

    def set_youtube_upload_session(self, job_id: str, value: str) -> None:
        self._set(f"{YOUTUBE_SESSION_PREFIX}{job_id}", value)

    def delete_youtube_upload_session(self, job_id: str) -> None:
        self._delete(f"{YOUTUBE_SESSION_PREFIX}{job_id}")


class MemorySecretStore:
    """Test-only secret store. It intentionally has no file persistence."""

    def __init__(self, value: str | None = None):
        self.value = value
        self.youtube_client: str | None = None
        self.youtube_tokens: dict[str, str] = {}
        self.youtube_sessions: dict[str, str] = {}

    def get_gemini_key(self) -> str | None:
        return self.value

    def set_gemini_key(self, value: str) -> None:
        self.value = value.strip() or None

    def delete_gemini_key(self) -> None:
        self.value = None

    def get_youtube_client(self) -> str | None:
        return self.youtube_client

    def set_youtube_client(self, value: str) -> None:
        self.youtube_client = value

    def get_youtube_token(self, credential_key: str) -> str | None:
        return self.youtube_tokens.get(credential_key)

    def set_youtube_token(self, credential_key: str, value: str) -> None:
        self.youtube_tokens[credential_key] = value

    def delete_youtube_token(self, credential_key: str) -> None:
        self.youtube_tokens.pop(credential_key, None)

    def get_youtube_upload_session(self, job_id: str) -> str | None:
        return self.youtube_sessions.get(job_id)

    def set_youtube_upload_session(self, job_id: str, value: str) -> None:
        self.youtube_sessions[job_id] = value

    def delete_youtube_upload_session(self, job_id: str) -> None:
        self.youtube_sessions.pop(job_id, None)
