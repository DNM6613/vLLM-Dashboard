from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 5174

    API_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("VLLM_DASHBOARD_API_KEY", "API_KEY"),
    )
    SSH_STRICT_HOST_KEY: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "VLLM_DASHBOARD_SSH_STRICT_HOST_KEY", "SSH_STRICT_HOST_KEY"),
    )

    VLLM_PYTHON_PATH: str = ""
    HF_ENDPOINT: str = "https://hf-mirror.com"
    PIP_INDEX_URL: str = "https://mirrors.aliyun.com/pypi/simple/"

    API_TIMEOUT: float = 10.0
    BENCHMARK_TIMEOUT: float = 60.0
    STATUS_SYNC_TIMEOUT: float = 5.0
    SSH_CMD_TIMEOUT: int = 10

    @property
    def SSH_CONNECT_TIMEOUT(self) -> int:
        return max(1, self.SSH_CMD_TIMEOUT // 2)

    @field_validator('API_PORT')
    @classmethod
    def validate_api_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("API_PORT must be between 1 and 65535")
        return v

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
