import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ServerStatus(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    UNKNOWN = "unknown"

CREDENTIAL_MASK = "***"

def has_parent_path_segment(path: str) -> bool:
    return ".." in path.split("/")

def _check_reserved_ipv4(octets: list[int], field: str) -> None:
    if (
        octets[0] in (0, 127)
        or (octets[0] == 169 and octets[1] == 254)
        or octets[0] >= 224
    ):
        raise ValueError(
            f"{field} is in a reserved range (0.0.0.0/8, 127.0.0.0/8, "
            "169.254.0.0/16, or 224.0.0.0/4+); a routable IPv4 address is required"
        )

class ServerConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: str = "default"
    host: str = ""
    port: int = Field(8000, ge=1, le=65535)
    api_key: str | None = None
    use_auth: bool = False
    extra_headers: dict[str, str] = {}

    ssh_port: int = Field(22, ge=1, le=65535)
    ssh_username: str = ""
    ssh_password: str | None = None
    ssh_key_path: str | None = None

    bmc_host: str = ""
    bmc_username: str = ""
    bmc_password: str | None = None

    venv_name: str = ".vllm"

    model_save_path: str = ""

    @field_validator('venv_name')
    @classmethod
    def validate_venv_name(cls, v: str) -> str:
        if not v:
            return ""
        if has_parent_path_segment(v):
            raise ValueError("Path traversal not allowed in venv_name")
        if not re.fullmatch(r'[\w/\-.~]+', v):
            raise ValueError("Invalid characters in venv_name (only alphanumeric / - _ . ~ allowed)")
        return v

    @field_validator('model_save_path', mode='before')
    @classmethod
    def validate_model_save_path(cls, v):
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("model_save_path must be a string")
        v = v.strip()
        if not v:
            return ""
        if len(v) > 512:
            raise ValueError("model_save_path too long (max 512 chars)")
        if has_parent_path_segment(v):
            raise ValueError("Path traversal not allowed in model_save_path")
        if not re.fullmatch(r'[\w/\-.~:@]+', v):
            raise ValueError("Invalid characters in model_save_path")
        return v

    @field_validator('ssh_password', mode='before')
    @classmethod
    def validate_ssh_password(cls, v):
        if v is not None and not isinstance(v, str):
            raise ValueError("ssh_password must be a string")
        if v and len(v) > 1024:
            raise ValueError("SSH password too long (max 1024 chars)")
        return v

    @field_validator('ssh_key_path', mode='before')
    @classmethod
    def validate_ssh_key_path(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("ssh_key_path must be a string")
        v = v.strip()
        if not v:
            return v
        if len(v) > 512:
            raise ValueError("ssh_key_path too long (max 512 chars)")
        if has_parent_path_segment(v):
            raise ValueError("Path traversal not allowed in ssh_key_path")
        if not re.fullmatch(r'[\w/\-.~:@]+', v):
            raise ValueError("Invalid characters in ssh_key_path")
        return v

    @field_validator('host', mode='before')
    @classmethod
    def validate_host(cls, v):
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("host must be a string")
        v = v.strip()
        if not v:
            return ""
        if v == "localhost":
            return v
        if not re.fullmatch(r'\d{1,3}(\.\d{1,3}){3}', v):
            raise ValueError("host must be an IPv4 address")
        octets = [int(o) for o in v.split('.')]
        if any(o > 255 for o in octets):
            raise ValueError("host must be a valid IPv4 address")
        _check_reserved_ipv4(octets, "host")
        return v

    @field_validator('bmc_host', mode='before')
    @classmethod
    def validate_bmc_host(cls, v):
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("bmc_host must be a string")
        v = v.strip()
        if not v:
            return ""
        if not re.fullmatch(r'\d{1,3}(\.\d{1,3}){3}', v):
            raise ValueError("bmc_host must be an IPv4 address")
        octets = [int(o) for o in v.split('.')]
        if any(o > 255 for o in octets):
            raise ValueError("bmc_host must be a valid IPv4 address")
        _check_reserved_ipv4(octets, "bmc_host")
        return v

    @field_validator('bmc_password', mode='before')
    @classmethod
    def validate_bmc_password(cls, v):
        if v is not None and not isinstance(v, str):
            raise ValueError("bmc_password must be a string")
        if v and len(v) > 1024:
            raise ValueError("BMC password too long (max 1024 chars)")
        return v

    @field_validator('api_key', mode='before')
    @classmethod
    def validate_api_key(cls, v):
        if v is not None and not isinstance(v, str):
            raise ValueError("api_key must be a string")
        if v and len(v) > 256:
            raise ValueError("api_key too long (max 256 chars)")
        return v

    @field_validator('extra_headers', mode='before')
    @classmethod
    def validate_extra_headers(cls, v):
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("extra_headers must be a dict")
        if len(v) > 20:
            raise ValueError("extra_headers has too many entries (max 20)")
        for k, val in v.items():
            if not isinstance(k, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,256}', k):
                raise ValueError("extra_headers keys must be 1-256 chars of [A-Za-z0-9-]")
            if not isinstance(val, str) or len(val) > 256:
                raise ValueError("extra_headers values must be strings (max 256 chars)")
        return v

    def get_base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def get_api_url(self, path: str = "") -> str:
        base = self.get_base_url()
        if path:
            return f"{base}/{path.lstrip('/')}"
        return base

    def is_remote(self) -> bool:
        return self.host != "" and self.host not in ("localhost", "127.0.0.1")

class ConnectionDetail(BaseModel):
    status: ServerStatus
    latency_ms: float | None = None
    error: str | None = None
    version: str | None = None

class DetailedConnectionStatus(BaseModel):
    http: ConnectionDetail | None = None
    ssh: ConnectionDetail | None = None
    overall: ServerStatus = ServerStatus.UNKNOWN
