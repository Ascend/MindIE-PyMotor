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
import os
import tempfile
from unittest.mock import Mock, patch

import pytest
import requests
from motor.utils.http_client import SafeHTTPSClient


@pytest.fixture
def base_url():
    return "https://api.example.com"


@pytest.fixture
def cert_files():
    """test certificate files"""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.crt') as cert_file:
        cert_file.write("test_cert_content")
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.key') as key_file:
        key_file.write("test_key_content")
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.ca') as ca_file:
        ca_file.write("test_ca_content")

    yield cert_file.name, key_file.name, ca_file.name

    # clear file
    os.unlink(cert_file.name)
    os.unlink(key_file.name)
    os.unlink(ca_file.name)


def test_init_with_valid_parameters(base_url, cert_files):
    """test init with valid parameters"""
    cert_file, key_file, ca_file = cert_files

    client = SafeHTTPSClient(
        base_url=base_url,
        cert_file=cert_file,
        key_file=key_file,
        ca_file=ca_file,
        timeout=10
    )

    assert client.base_url == base_url
    assert client.timeout == 10
    assert client.cert == (cert_file, key_file)
    assert client.verify == ca_file
    assert 'User-Agent' in client.session.headers
    assert client.session.headers['User-Agent'] == 'Secure-HTTPS-Client/1.0'


def test_init_with_missing_cert_files(base_url):
    """test init with missing cert files"""
    with pytest.raises(FileNotFoundError, match="can not find cert file or key file."):
        SafeHTTPSClient(
            base_url=base_url,
            cert_file="nonexistent.crt",
            key_file="nonexistent.key"
        )


def test_init_without_certificates(base_url):
    """test init without certs"""
    client = SafeHTTPSClient(base_url=base_url)

    assert client.cert is None
    assert client.verify is True


def test_url_construction(base_url):
    """test url construction"""
    client = SafeHTTPSClient(base_url=base_url)

    client_with_slash = SafeHTTPSClient(base_url=base_url + "/")
    assert client_with_slash.base_url == base_url

    with patch.object(client.session, 'request') as mock_request:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        mock_request.return_value = mock_response

        client.get("/test-endpoint")
        called_url = mock_request.call_args[1]['url']
        assert called_url == f"{base_url}/test-endpoint"

        client.get("test-endpoint")
        called_url = mock_request.call_args[1]['url']
        assert called_url == f"{base_url}/test-endpoint"


@pytest.mark.parametrize("method", ['GET', 'POST'])
def test_successful_requests(base_url, method):
    """test successful requests"""
    client = SafeHTTPSClient(base_url=base_url)

    with patch.object(client.session, 'request') as mock_request:
        expected_response = {"status": "success", "data": "test"}
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = expected_response
        mock_request.return_value = mock_response

        if method == 'GET':
            response = client.get("/test", params={"key": "value"})
        else:
            response = client.post("/test", data={"key": "value"})

        assert response == expected_response

        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs['method'] == method
        assert call_kwargs['url'] == f"{base_url}/test"


def test_ssl_error_handling(base_url):
    """test ssl error handling"""
    client = SafeHTTPSClient(base_url=base_url)

    with patch.object(client.session, 'request') as mock_request:
        mock_request.side_effect = requests.exceptions.SSLError("SSL certificate verification failed")

        with pytest.raises(Exception, match="SSL verify failed:"):
            client.get("/test")


def test_http_error_handling(base_url):
    """test http error handling"""
    client = SafeHTTPSClient(base_url=base_url)

    with patch.object(client.session, 'request') as mock_request:
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_request.side_effect = requests.exceptions.HTTPError(response=mock_response)

        with pytest.raises(Exception, match="http response error 404"):
            client.get("/test")


def test_generic_exception_handling(base_url):
    """test generic exception handling"""
    client = SafeHTTPSClient(base_url=base_url)

    with patch.object(client.session, 'request') as mock_request:
        mock_request.side_effect = Exception("Generic error")

        with pytest.raises(Exception, match="send request error."):
            client.get("/test")


def test_context_manager(base_url):
    """test context manager"""
    with patch.object(requests.Session, 'close') as mock_close:
        with SafeHTTPSClient(base_url=base_url) as client:
            assert isinstance(client, SafeHTTPSClient)

        mock_close.assert_called_once()


def test_close_method(base_url):
    """test close method"""
    client = SafeHTTPSClient(base_url=base_url)

    with patch.object(client.session, 'close') as mock_close:
        client.close()
        mock_close.assert_called_once()


def test_request_timeout(base_url):
    """test request timeout"""
    client = SafeHTTPSClient(base_url=base_url, timeout=3.5)

    with patch.object(client.session, 'request') as mock_request:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_request.return_value = mock_response

        client.get("/test")

        call_kwargs = mock_request.call_args[1]
        assert call_kwargs['timeout'] == 3.5
