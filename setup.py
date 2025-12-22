# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.

import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from functools import partial
from setuptools import setup, find_packages
from setuptools.command.build_py import build_py
from setuptools.command.build import build

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
UTF8_ENCODING = 'utf-8'


def generate_proto_files():
    """Generate Python code from .proto files."""
    try:
        # Check if grpcio-tools is available
        import grpc_tools.protoc
    except ImportError:
        logging.warning("grpcio-tools is not installed. Skipping protobuf generation.")
        logging.info("Please install it with: pip install grpcio-tools>=1.40.0")
        return

    # Get the project root directory
    root_dir = Path(__file__).parent.absolute()

    # Find all .proto files
    proto_files = list(root_dir.rglob("*.proto"))

    if not proto_files:
        logging.info("No .proto files found.")
        return

    # Generate Python code for each .proto file
    for proto_file in proto_files:
        logging.info(f"Generating code from {proto_file.relative_to(root_dir)}...")

        proto_dir = proto_file.parent
        proto_base = proto_file.stem  # filename without extension

        # Generate _pb2.py and _pb2_grpc.py files
        # Change to proto directory for protoc execution (protoc requires proto_path to match file location)
        original_cwd = os.getcwd()
        try:
            os.chdir(proto_dir)
            subprocess.check_call([
                sys.executable, "-m", "grpc_tools.protoc",
                "--proto_path=.",
                "--python_out=.",
                "--grpc_python_out=.",
                proto_file.name
            ])
        finally:
            os.chdir(original_cwd)

        # Fix import paths in _pb2_grpc.py if it exists
        pb2_grpc_file = proto_dir / f"{proto_base}_pb2_grpc.py"
        if pb2_grpc_file.exists():
            # Get the directory path, not the file path
            proto_rel_path = proto_file.relative_to(root_dir)
            package_path = str(proto_rel_path.parent).replace('/', '.').replace('\\', '.')

            # Read the file and fix imports
            content = pb2_grpc_file.read_text(encoding=UTF8_ENCODING)
            # Replace relative import with absolute import
            if package_path:
                # Only replace if it's actually a relative import (not already absolute)
                old_import_relative = f'import {proto_base}_pb2'
                new_import_absolute = f'from {package_path} import {proto_base}_pb2'
                if (
                    old_import_relative in content
                    and f'from {package_path} import {proto_base}_pb2' not in content
                ):
                    content = content.replace(old_import_relative, new_import_absolute)
                    logging.info(f"  Fixed import: {old_import_relative} -> {new_import_absolute}")

                old_import_as_relative = f'import {proto_base}_pb2 as'
                new_import_as_absolute = f'from {package_path} import {proto_base}_pb2 as'
                if (
                    old_import_as_relative in content
                    and f'from {package_path} import {proto_base}_pb2 as' not in content
                ):
                    content = content.replace(old_import_as_relative, new_import_as_absolute)
                    logging.info(f"  Fixed import: {old_import_as_relative}* -> {new_import_as_absolute}*")

            pb2_grpc_file.write_text(content, encoding=UTF8_ENCODING)
            logging.info(f"  Fixed import paths in {proto_base}_pb2_grpc.py")

            # Fix import paths in _pb2.py if it exists
            pb2_file = proto_dir / f"{proto_base}_pb2.py"
            if pb2_file.exists():
                # Get the directory path, not the file path
                proto_rel_path = proto_file.relative_to(root_dir)
                package_path = str(proto_rel_path.parent).replace('/', '.').replace('\\', '.')

                # find all _pb2 file in same dictionary
                sibling_pb2_modules = set()
                for f in proto_dir.glob("*_pb2.py"):
                    sibling_pb2_modules.add(f.stem)  # e.g., "kv_pb2"

                # read
                content = pb2_file.read_text(encoding=UTF8_ENCODING)

                replacer = partial(
                    replace_import,
                    sibling_pb2_modules=sibling_pb2_modules,
                    package_path=package_path
                )

                # match: import xxx_pb2, import xxx_pb2 as yyy
                pattern = r'\bimport\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(as\s+\w+)?(?=\s|$|#)'
                content = re.sub(pattern, replacer, content)

                pb2_file.write_text(content, encoding=UTF8_ENCODING)
                logging.info(f"  Fixed import paths in {pb2_file.name}")

        logging.info(f"✓ Successfully generated code from {proto_file.name}")


# reg match import xxx_pb2 / import xxx_pb2 as yyy
def replace_import(match, sibling_pb2_modules: set[str], package_path: str):
    """
    Replace 'import xxx_pb2' with 'from package import xxx_pb2' if it's a sibling module.
    """
    full_match = match.group(0)
    module_name = match.group(1)      # e.g., "kv_pb2"
    alias_part = match.group(2)  # e.g., "as yyy" or ""

    # only process sibling modules
    if module_name in sibling_pb2_modules:
        if package_path:
            new_import = f"from {package_path} import {module_name} {alias_part}"
        else:
            # if package_path is empty(root) , keep it
            new_import = full_match
        logging.info(f"  Fixed import: {full_match} -> {new_import}")
        return new_import
    else:
        return full_match


class BuildCommand(build):
    """Custom build command to generate protobuf files before building."""

    def run(self):
        # Generate protobuf files before building
        generate_proto_files()
        # Run the standard build command
        super().run()


class BuildPyCommand(build_py):
    """Custom build_py command to generate protobuf files before building."""

    def run(self):
        # Generate protobuf files before building
        generate_proto_files()
        # Run the standard build_py command
        super().run()


setup(
    name="motor",
    version="0.1.0",
    description="A Python package named motor.",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[

    ],
    include_package_data=True,
    zip_safe=False,
    cmdclass={
        'build': BuildCommand,
        'build_py': BuildPyCommand,
    },
    entry_points={
        "console_scripts": [
            "engine_server = motor.engine_server.cli.main:main",
        ]
    }
)
