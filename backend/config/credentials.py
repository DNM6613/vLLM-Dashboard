"""Credential encryption for the persisted server config (Fernet).

Split out of ``remote_client.py`` (file-size gate). Reuses the legacy
``data/.secret_key`` so already-encrypted values keep decrypting.
"""

from __future__ import annotations

import contextlib
import logging
import os

logger = logging.getLogger(__name__)

_CRED_FIELDS = ("ssh_password", "bmc_password", "api_key", "hf_token")


def _get_legacy_secret_key_file() -> str:
    current_file = os.path.abspath(__file__)
    # config/credentials.py -> project root is three levels up (config ->
    # backend -> project); kept identical to the original remote_client path
    # resolution so existing key files keep working.
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
    return os.path.join(project_root, "data", ".secret_key")


def _migrate_legacy_encrypted(stored: str) -> str:
    if not stored or not stored.startswith("enc:"):
        return stored
    key_file = _get_legacy_secret_key_file()
    if not os.path.exists(key_file):
        logger.warning(
            "配置含旧版加密密码但密钥文件缺失（%s），密码已清空，请重新设置", key_file
        )
        return ""
    try:
        from cryptography.fernet import Fernet
        with open(key_file, "rb") as f:
            fernet = Fernet(f.read())
        return fernet.decrypt(stored[4:].encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.warning(
            "旧版加密密码解密失败（密码已清空，请重新设置）: %s", e
        )
        return ""


def _fernet():
    """Return a Fernet instance, creating the key file if it does not exist yet.

    Reuses the legacy ``data/.secret_key`` so already-encrypted values keep
    decrypting; a new key is only generated on first save.
    """
    from cryptography.fernet import Fernet
    key_file = _get_legacy_secret_key_file()
    if os.path.exists(key_file):
        with open(key_file, "rb") as f:
            return Fernet(f.read())
    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    with open(key_file, "wb") as f:
        f.write(key)
    if os.name == "posix":
        with contextlib.suppress(OSError):
            os.chmod(key_file, 0o600)
    return Fernet(key)


def _encrypt_credential(fernet, value: str) -> str:
    return "enc:" + fernet.encrypt(value.encode("utf-8")).decode("utf-8")
