#!/usr/bin/env python
# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.
"""
API Key generation utility.

This script generates encrypted API Keys for configuring MindIE-pyMotor services.

Usage:
    python deployer/api_key/generate_api_key.py [--key <plain_api_key>] [--algorithm <name>] [--iterations <N>]

Examples:
    # Generate a random API Key using the default algorithm (PBKDF2_SHA256)
    python deployer/api_key/generate_api_key.py

    # Specify a plain API Key
    python deployer/api_key/generate_api_key.py --key "sk-test123456789"

    # Custom PBKDF2 iteration count (default 100000)
    python deployer/api_key/generate_api_key.py --iterations 200000
"""

import sys
import argparse
import secrets
import os
import logging
from pathlib import Path

# Set Windows console encoding to UTF-8 when supported
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        # Python < 3.7 does not support reconfigure
        pass

# Configure logging: output to stdout with a concise format
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    stream=sys.stdout,
    force=True  # Force reconfiguration to avoid being overridden by other modules
)

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from motor.common.utils.key_encryption import (
    PBKDF2KeyEncryption,
    get_supported_algorithms,
    set_default_key_encryption_by_name,
)


def generate_api_key(
    plain_key: str = None,
    algorithm: str = "PBKDF2_SHA256",
    iterations: int = 100000,
) -> tuple[str, str]:
    """
    Generate an encrypted API Key.

    Args:
        plain_key: Plain API Key; if None, one is generated automatically.
        algorithm: Encryption algorithm name; defaults to PBKDF2_SHA256.
        iterations: PBKDF2 iteration count (only used for PBKDF2_SHA256). Default 100000.

    Returns:
        A (plain_key, encrypted_key) tuple.
    """
    # Auto-generate a plain key if none was provided
    if not plain_key:
        plain_key = f"sk-{secrets.token_urlsafe(32)}"
        logging.info(f"[INFO] Auto-generated plain API Key: {plain_key}")

    # Set the default encryption algorithm
    try:
        set_default_key_encryption_by_name(algorithm)
    except ValueError as e:
        logging.error(f"[ERROR] Unsupported encryption algorithm '{algorithm}'")
        logging.error(f"Supported algorithms: {', '.join(get_supported_algorithms())}")
        # Re-raise so the caller can handle it; the library does not exit the process
        raise

    # Generate the encrypted key; pass iterations for PBKDF2
    if algorithm == "PBKDF2_SHA256":
        encrypted_key = PBKDF2KeyEncryption.encrypt_key(plain_key, iterations=iterations)
    else:
        encrypted_key = PBKDF2KeyEncryption.encrypt_key(plain_key)

    return plain_key, encrypted_key


def main():
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Generate MindIE-pyMotor API Key",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate a random API Key
  python deployer/api_key/generate_api_key.py

  # Specify a plain API Key
  python deployer/api_key/generate_api_key.py --key "sk-test123456789"

  # Use a specific algorithm
  python deployer/api_key/generate_api_key.py --key "sk-test123456789" --algorithm "PBKDF2_SHA256"

  # Custom PBKDF2 iteration count (default 100000)
  python deployer/api_key/generate_api_key.py --iterations 200000
        """
    )

    parser.add_argument(
        "--key",
        type=str,
        default=None,
        help="Plain API Key (if omitted, a random key is generated)"
    )

    parser.add_argument(
        "--algorithm",
        type=str,
        default="PBKDF2_SHA256",
        choices=get_supported_algorithms(),
        help=f"Encryption algorithm (default: PBKDF2_SHA256). Supported: {', '.join(get_supported_algorithms())}"
    )

    def positive_int(s: str) -> int:
        v = int(s)
        if v < 1:
            raise argparse.ArgumentTypeError("iterations must be >= 1")
        return v

    parser.add_argument(
        "--iterations",
        type=positive_int,
        default=100000,
        metavar="N",
        help="PBKDF2 iteration count (default: 100000). Only applies to PBKDF2_SHA256."
    )

    args = parser.parse_args()

    logging.info("=" * 60)
    logging.info("MindIE-pyMotor API Key Generator")
    logging.info("=" * 60)
    logging.info("")

    try:
        plain_key, encrypted_key = generate_api_key(
            args.key, args.algorithm, iterations=args.iterations
        )

        logging.info("")
        logging.info("[SUCCESS] API Key generated successfully.")
        logging.info("")
        logging.info("Add the following to your config:")
        logging.info("")
        logging.info("1. Plain API Key (for client requests):")
        logging.info(f"   {plain_key}")
        logging.info("")
        logging.info("2. Encrypted API Key (for config file):")
        logging.info(f"   {encrypted_key}")
        logging.info("")
        logging.info("Config example (deployer/user_config.json):")
        logging.info("   {")
        logging.info('     "motor_coordinator_config": {')
        logging.info('       "api_key_config": {')
        logging.info('         "enable_api_key": true,')
        logging.info(f'         "valid_keys": ["{encrypted_key}"],')
        logging.info('         "encryption_algorithm": "' + args.algorithm + '"')
        logging.info("       }")
        logging.info("     }")
        logging.info("   }")
        logging.info("")
        logging.info("=" * 60)

    except Exception as e:
        logging.error(f"[ERROR] {e}")
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
