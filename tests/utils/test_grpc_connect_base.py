# coding=utf-8
# Copyright (c) 2025, HUAWEI CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from unittest.mock import Mock, patch, mock_open
import pytest

from motor.utils.grpc_connect_base import GrpcSecureClientBase


@pytest.fixture
def base_client_without_ssl():
    """create base client without ssl"""
    return GrpcSecureClientBase(
        host="localhost",
        port="50051",
        is_ssl_secure=False
    )


@pytest.fixture
def base_client_with_ssl():
    """create base client with ssl"""
    return GrpcSecureClientBase(
        host="localhost",
        port="50051",
        is_ssl_secure=True,
        root_cert="test_root.crt",
        cert_file="test_cert.crt",
        key_file="test_key.key"
    )


@pytest.fixture
def mock_cert_files():
    """mock cert files content"""
    return {
        "root_cert": b"mock_root_cert_content",
        "cert_file": b"mock_cert_content",
        "key_file": b"mock_key_content"
    }


def test_init_without_ssl(base_client_without_ssl):
    """test grpc-secure-client-init without ssl"""
    assert base_client_without_ssl._host == "localhost"
    assert base_client_without_ssl._port == "50051"
    assert base_client_without_ssl._is_ssl_secure is False
    assert base_client_without_ssl._root_cert is None
    assert base_client_without_ssl._cert_file is None
    assert base_client_without_ssl._key_file is None


def test_init_with_ssl(base_client_with_ssl):
    """test grpc-secure-client-init with ssl"""
    assert base_client_with_ssl._host == "localhost"
    assert base_client_with_ssl._port == "50051"
    assert base_client_with_ssl._is_ssl_secure is True
    assert base_client_with_ssl._root_cert == "test_root.crt"
    assert base_client_with_ssl._cert_file == "test_cert.crt"
    assert base_client_with_ssl._key_file == "test_key.key"


def test_load_ssl_credentials_without_ssl(base_client_without_ssl):
    """test grpc-secure-client-load without ssl"""
    with patch('grpc.ssl_channel_credentials') as mock_ssl_creds:
        credentials = base_client_without_ssl._load_ssl_credentials()

        mock_ssl_creds.assert_called_once_with()
        assert credentials == mock_ssl_creds.return_value


def test_load_ssl_credentials_with_ssl(base_client_with_ssl, mock_cert_files):
    """test grpc-secure-client-load with ssl"""
    with patch('builtins.open', mock_open(read_data=b"mock_content")) as mock_file, \
            patch('grpc.ssl_channel_credentials') as mock_ssl_creds:
        credentials = base_client_with_ssl._load_ssl_credentials()

        # verify file is opened correctly
        assert mock_file.call_count == 3
        mock_file.assert_any_call("test_root.crt", 'rb')
        mock_file.assert_any_call("test_cert.crt", 'rb')
        mock_file.assert_any_call("test_key.key", 'rb')

        # verify ssl_channel_credentials is called correctly
        mock_ssl_creds.assert_called_once_with(
            root_certificates=b"mock_content",
            private_key=b"mock_content",
            certificate_chain=b"mock_content"
        )
        assert credentials == mock_ssl_creds.return_value


def test_load_ssl_credentials_file_not_found(base_client_with_ssl):
    """test grpc-secure-client-load with ssl file not found"""
    with patch('builtins.open', side_effect=FileNotFoundError("File not found")):
        with pytest.raises(Exception) as exc_info:
            base_client_with_ssl._load_ssl_credentials()

        assert "Failed to load SSL credentials" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, FileNotFoundError)


def test_load_ssl_credentials_io_error(base_client_with_ssl):
    """test grpc-secure-client-load with io error"""
    with patch('builtins.open', side_effect=IOError("IO Error")):
        with pytest.raises(Exception) as exc_info:
            base_client_with_ssl._load_ssl_credentials()

        assert "Failed to load SSL credentials" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, IOError)


def test_create_secure_channel_without_ssl(base_client_without_ssl):
    """test grpc-secure-client-create without ssl"""
    mock_credentials = Mock()
    options = [('grpc.keepalive_time_ms', 10000)]

    with patch.object(base_client_without_ssl, '_load_ssl_credentials', return_value=mock_credentials), \
            patch('grpc.secure_channel') as mock_secure_channel:
        channel = base_client_without_ssl.create_secure_channel(options=options)

        mock_secure_channel.assert_called_once_with(
            'localhost:50051',
            mock_credentials,
            options=options
        )
        assert channel == mock_secure_channel.return_value


def test_create_secure_channel_with_ssl(base_client_with_ssl):
    """test grpc-secure-client-create with ssl"""
    mock_credentials = Mock()
    options = [('grpc.keepalive_time_ms', 10000)]

    with patch.object(base_client_with_ssl, '_load_ssl_credentials', return_value=mock_credentials), \
            patch('grpc.secure_channel') as mock_secure_channel:
        channel = base_client_with_ssl.create_secure_channel(options=options)

        mock_secure_channel.assert_called_once_with(
            'localhost:50051',
            mock_credentials,
            options=options
        )
        assert channel == mock_secure_channel.return_value


def test_create_secure_channel_default_options(base_client_without_ssl):
    """test grpc-secure-client-create with default options"""
    mock_credentials = Mock()

    with patch.object(base_client_without_ssl, '_load_ssl_credentials', return_value=mock_credentials), \
            patch('grpc.secure_channel') as mock_secure_channel:
        channel = base_client_without_ssl.create_secure_channel()

        mock_secure_channel.assert_called_once_with(
            'localhost:50051',
            mock_credentials,
            options=None
        )


def test_create_secure_channel_connection_error(base_client_without_ssl):
    """test grpc-secure-client-create with connection error"""
    with patch.object(base_client_without_ssl, '_load_ssl_credentials', side_effect=Exception("Connection failed")):
        with pytest.raises(Exception) as exc_info:
            base_client_without_ssl.create_secure_channel()

        assert "Failed to create secure channel" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, Exception)


def test_connect_not_implemented(base_client_without_ssl):
    """test grpc-secure-client-connect not implemented"""
    with pytest.raises(NotImplementedError):
        base_client_without_ssl.connect()


@pytest.mark.parametrize("host,port,expected_address", [
    ("localhost", "50051", "localhost:50051"),
    ("127.0.0.1", "8080", "127.0.0.1:8080"),
    ("example.com", "443", "example.com:443"),
])
def test_channel_address_format(host, port, expected_address):
    """test grpc-secure-client-connect channel address format"""
    client = GrpcSecureClientBase(host=host, port=port, is_ssl_secure=False)

    with patch.object(client, '_load_ssl_credentials', return_value=Mock()), \
            patch('grpc.secure_channel') as mock_secure_channel:
        client.create_secure_channel()

        mock_secure_channel.assert_called_once()
        call_args = mock_secure_channel.call_args[0]
        assert call_args[0] == expected_address


def test_ssl_credentials_parameters_order(base_client_with_ssl):
    """test grpc-secure-client-connect with ssl parameters"""
    with patch('builtins.open', mock_open(read_data=b"content")) as mock_file, \
            patch('grpc.ssl_channel_credentials') as mock_ssl_creds:
        base_client_with_ssl._load_ssl_credentials()

        mock_ssl_creds.assert_called_once()
        call_kwargs = mock_ssl_creds.call_args[1]
        assert 'root_certificates' in call_kwargs
        assert 'private_key' in call_kwargs
        assert 'certificate_chain' in call_kwargs


class TestConcreteImplementation:
    """test grpc-secure-client-connect with concrete implementation"""

    class ConcreteGrpcClient(GrpcSecureClientBase):
        """test grpc-secure-client-connect with concrete implementation"""

        def connect(self):
            """connect to grpc-secure-client"""
            channel = self.create_secure_channel()
            return channel

    def test_concrete_implementation(self):
        """test grpc-secure-client-connect with concrete implementation"""
        client = self.ConcreteGrpcClient("localhost", "50051", False)

        mock_channel = Mock()
        with patch.object(client, 'create_secure_channel', return_value=mock_channel):
            result = client.connect()
            assert result == mock_channel
            client.create_secure_channel.assert_called_once()


@pytest.fixture
def create_test_cert_files(tmp_path):
    """create test cert files"""
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()

    root_cert = cert_dir / "root.crt"
    cert_file = cert_dir / "cert.crt"
    key_file = cert_dir / "key.key"
    root_cert.write_bytes(b"test_root_cert")
    cert_file.write_bytes(b"test_cert")
    key_file.write_bytes(b"test_key")

    return str(root_cert), str(cert_file), str(key_file)


def test_integration_with_ssl_files(create_test_cert_files):
    """test grpc-secure-client-connect with ssl files"""
    root_cert, cert_file, key_file = create_test_cert_files

    client = GrpcSecureClientBase(
        host="localhost",
        port="50051",
        is_ssl_secure=True,
        root_cert=root_cert,
        cert_file=cert_file,
        key_file=key_file
    )

    with patch('grpc.ssl_channel_credentials') as mock_ssl_creds:
        credentials = client._load_ssl_credentials()

        mock_ssl_creds.assert_called_once()
        call_kwargs = mock_ssl_creds.call_args[1]

        assert call_kwargs['root_certificates'] == b"test_key"
        assert call_kwargs['private_key'] == b"test_root_cert"
        assert call_kwargs['certificate_chain'] == b"test_cert"
