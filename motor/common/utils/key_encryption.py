# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.

import hashlib
import hmac
import secrets
from abc import ABC, abstractmethod
from typing import Iterable, Optional


class KeyEncryptionBase(ABC):
    """Base class for API key encryption strategies"""

    @classmethod
    @abstractmethod
    def encrypt_key(cls, plain_key: str) -> str:
        """
        Encrypt a plain API key

        Args:
            plain_key: The plain text API key to encrypt

        Returns:
            The encrypted key as a string
        """
        pass

    @abstractmethod
    def verify_key(self, plain_key: str, encrypted_key: str) -> bool:
        """
        Verify if a plain key matches an encrypted key

        Args:
            plain_key: The plain text key to verify
            encrypted_key: The encrypted key to compare against

        Returns:
            True if keys match, False otherwise
        """
        pass

    @abstractmethod
    def get_algorithm_name(self) -> str:
        """
        Get the name of the encryption algorithm

        Returns:
            Algorithm name as string
        """
        pass


class PBKDF2KeyEncryption(KeyEncryptionBase):
    """PBKDF2-based key encryption using salt and iterations"""

    def __init__(self, salt: Optional[str] = None, iterations: Optional[int] = 100000):
        """
        Initialize PBKDF2 encryption

        Args:
            salt: Salt for hashing. Must be provided before encryption
            iterations: Number of iterations for PBKDF2 (default: 100000)
        """
        self.salt = salt
        self.iterations = iterations

    @classmethod
    def generate_salt(cls) -> str:
        """
        Generate a random salt for PBKDF2

        Returns:
            Random salt as a hex string
        """
        return secrets.token_hex(16)

    @classmethod
    def encrypt_key(cls, plain_key: str, salt: Optional[str] = None, iterations: Optional[int] = 100000) -> str:
        """
        Encrypt key using PBKDF2 with salt and iterations

        Args:
            plain_key: Plain text key
            salt: Salt for hashing. If None, a random salt will be generated
            iterations: Number of iterations for PBKDF2 (default: 100000)

        Returns:
            Encrypted key in format: salt:iterations:pbkdf2_hash
        
        Raises:
            ValueError: If salt is not provided or plain_key is empty
        """
        if not plain_key:
            raise ValueError("Plain key cannot be empty")
        
        if not salt:
            salt = cls.generate_salt()

        salt_bytes = salt.encode('utf-8')
        key_bytes = plain_key.encode('utf-8')
        derived_key = hashlib.pbkdf2_hmac(
            'sha256',  # Hash digest algorithm
            key_bytes,  # Password to hash
            salt_bytes,  # Salt
            iterations,  # Iterations
            dklen=32  # Key length (32 bytes for SHA256)
        )
        encrypted = derived_key.hex()

        # Return salt:iterations:hash format for storage
        return f"{salt}:{iterations}:{encrypted}"

    @classmethod
    def get_salt(cls, encrypted_key: str) -> str:
        """
        Get salt from encrypted key
        Args:
            encrypted_key: Stored encrypted key in format salt:iterations:hash
        Returns:
            Salt used for encryption
        """
        # Parse stored format: salt:iterations:encrypted_hash
        parts = encrypted_key.split(':', 2)
        if len(parts) != 3:
            raise ValueError("Invalid encrypted key format")

        stored_salt, _, _ = parts
        return stored_salt

    def verify_key(self, plain_key: str, encrypted_key: str) -> bool:
        """
        Verify key against encrypted version

        Args:
            plain_key: Plain text key to verify
            encrypted_key: Stored encrypted key in format salt:iterations:hash

        Returns:
            True if keys match
        """
        if not plain_key or not encrypted_key:
            return False

        try:
            # Parse stored format: salt:iterations:encrypted_hash
            parts = encrypted_key.split(':', 2)
            if len(parts) != 3:
                return False

            stored_salt, stored_iterations, stored_hash = parts

            # Convert iterations back to integer
            iterations = int(stored_iterations)

            # Recreate PBKDF2 with same parameters
            salt_bytes = stored_salt.encode('utf-8')
            key_bytes = plain_key.encode('utf-8')
            derived_key = hashlib.pbkdf2_hmac(
                'sha256',
                key_bytes,
                salt_bytes,
                iterations,
                dklen=32
            )
            computed_hash = derived_key.hex()

            # Use constant-time comparison to prevent timing attacks
            return hmac.compare_digest(computed_hash, stored_hash)

        except Exception:
            return False

    def get_algorithm_name(self) -> str:
        return "PBKDF2_SHA256"


# Built-in algorithm mapping
_builtin_algorithms = {
    "PBKDF2_SHA256": PBKDF2KeyEncryption,
}

# Dynamic encryption algorithm registry
_encryption_registry: dict[str, type[KeyEncryptionBase]] = {}
_default_encryption: Optional[KeyEncryptionBase] = None


def register_encryption_algorithm(name: str, algorithm_class: type[KeyEncryptionBase]) -> None:
    """
    Register an encryption algorithm

    Args:
        name: Algorithm name (e.g., "PBKDF2_SHA256")
        algorithm_class: The encryption class
    """
    _encryption_registry[name] = algorithm_class


def register_algorithm_from_config(algorithm_name: str) -> None:
    """
    Register an algorithm based on configuration name

    Args:
        algorithm_name: The algorithm name from configuration

    Raises:
        ValueError: If algorithm is not supported
    """
    if algorithm_name in _builtin_algorithms:
        # Register built-in algorithm
        register_encryption_algorithm(algorithm_name, _builtin_algorithms[algorithm_name])
    else:
        raise ValueError(f"Unsupported encryption algorithm: {algorithm_name}. "
                        f"Supported: {list(_builtin_algorithms.keys())}")


def get_encryption_algorithm(name: str) -> KeyEncryptionBase:
    """
    Get an encryption algorithm instance by name

    Args:
        name: Algorithm name

    Returns:
        Encryption algorithm instance

    Raises:
        ValueError: If algorithm is not registered
    """
    if name not in _encryption_registry:
        available = list(_encryption_registry.keys())
        raise ValueError(f"Unknown encryption algorithm '{name}'. Available: {available}")

    return _encryption_registry[name]()


def set_default_key_encryption(encryption: KeyEncryptionBase) -> None:
    """Set the default key encryption instance"""
    global _default_encryption
    _default_encryption = encryption


def set_default_key_encryption_by_name(name: str) -> None:
    """
    Set the default key encryption by algorithm name.
    This will register the algorithm if not already registered.

    Args:
        name: Algorithm name

    Raises:
        ValueError: If algorithm is not supported
    """
    # Register algorithm if not already registered
    if name not in _encryption_registry:
        register_algorithm_from_config(name)

    # Get and set the algorithm
    encryption = get_encryption_algorithm(name)
    set_default_key_encryption(encryption)


def get_default_key_encryption() -> KeyEncryptionBase:
    """Get the default key encryption instance"""
    if _default_encryption is None:
        # Default to PBKDF2_SHA256
        set_default_key_encryption_by_name("PBKDF2_SHA256")
    return _default_encryption


def encrypt_api_key(plain_key: str) -> str:
    """Encrypt an API key using the default encryption"""
    return get_default_key_encryption().encrypt_key(plain_key)


def verify_api_key(plain_key: str, encrypted_key: str) -> bool:
    """Verify an API key against its encrypted version"""
    return get_default_key_encryption().verify_key(plain_key, encrypted_key)


def verify_api_key_against_valid_keys(plain_key: str, valid_keys: Iterable[str]) -> bool:
    """
    Verify a plain API key against a collection of encrypted keys (e.g. from config).

    Args:
        plain_key: The plain text key from the request.
        valid_keys: Encrypted keys to check against (e.g. api_key_config.valid_keys).

    Returns:
        True if the plain key matches any of the encrypted keys.
    """
    for encrypted_key in valid_keys:
        if verify_api_key(plain_key, encrypted_key):
            return True
    return False


def get_supported_algorithms() -> list[str]:
    """Get list of supported algorithm names"""
    return list(_builtin_algorithms.keys())
