"""TLL v2 安全模块 —— 应用层对称加密（保留 Fernet 契约）。

与旧 tll_protocol/security.py 完全兼容：
- 密钥派生：urlsafe_b64encode(sha256(auth_key).digest())
- encrypt_payload: 用目标 bot 的 auth_key 加密；空 key 原样返回
- decrypt_payload: 用自身 auth_key 解密；任何异常静默原样返回（由上层 json.loads 兜底）
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet


def _fernet_key(auth_key: str) -> bytes:
    """从 auth_key 派生 Fernet 密钥（32 字节 url-safe base64）。"""
    digest = hashlib.sha256(auth_key.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_payload(data: bytes, auth_key: str) -> bytes:
    """加密字节数据，若 auth_key 为空则原样返回。"""
    if not auth_key:
        return data
    f = Fernet(_fernet_key(auth_key))
    return f.encrypt(data)


def decrypt_payload(data: bytes, auth_key: str) -> bytes:
    """解密字节数据，若 auth_key 为空或解密失败则原样返回。"""
    if not auth_key:
        return data
    try:
        f = Fernet(_fernet_key(auth_key))
        return f.decrypt(data)
    except Exception:
        # 解密失败，可能是明文或密钥不匹配，交给上层处理
        return data
