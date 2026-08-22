"""
TLL 安全模块 - 应用层对称加密

使用目标 bot 的 auth_key 作为密钥，通过 Fernet 加密/解密。
"""

import base64
import hashlib

from cryptography.fernet import Fernet


def _fernet_key(auth_key: str) -> bytes:
    """从 auth_key 派生 Fernet 密钥（32字节 url-safe base64）"""
    digest = hashlib.sha256(auth_key.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_payload(data: bytes, auth_key: str) -> bytes:
    """加密字节数据，若 auth_key 为空则原样返回"""
    if not auth_key:
        return data
    f = Fernet(_fernet_key(auth_key))
    return f.encrypt(data)


def decrypt_payload(data: bytes, auth_key: str) -> bytes:
    """解密字节数据，若 auth_key 为空或解密失败则原样返回"""
    if not auth_key:
        return data
    try:
        f = Fernet(_fernet_key(auth_key))
        return f.decrypt(data)
    except Exception:
        # 解密失败，可能是明文或密钥不匹配，交给上层处理
        return data
