"""Tests for the tenacity-based retry decorator."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import openstack.exceptions
import pytest
import requests.exceptions

from src.utils import retry


@retry()
def _sample_func(mock: MagicMock) -> str:
    return mock()


class TestRetrySuccess:
    """Successful calls should pass through without retrying."""

    def test_returns_value_on_first_call(self) -> None:
        mock = MagicMock(return_value="ok")
        assert _sample_func(mock) == "ok"
        assert mock.call_count == 1


class TestRetryOnServerErrors:
    """5xx and 429 errors should trigger retries."""

    @patch("tenacity.nap.time")
    def test_retries_on_503(self, mock_sleep: MagicMock) -> None:
        exc = openstack.exceptions.HttpException(message="Service Unavailable")
        exc.status_code = 503
        mock = MagicMock(side_effect=[exc, "ok"])
        assert _sample_func(mock) == "ok"
        assert mock.call_count == 2

    @patch("tenacity.nap.time")
    def test_retries_on_429(self, mock_sleep: MagicMock) -> None:
        exc = openstack.exceptions.HttpException(message="Too Many Requests")
        exc.status_code = 429
        mock = MagicMock(side_effect=[exc, "ok"])
        assert _sample_func(mock) == "ok"
        assert mock.call_count == 2

    @patch("tenacity.nap.time")
    def test_retries_on_500(self, mock_sleep: MagicMock) -> None:
        exc = openstack.exceptions.HttpException(message="Internal Server Error")
        exc.status_code = 500
        mock = MagicMock(side_effect=[exc, "ok"])
        assert _sample_func(mock) == "ok"
        assert mock.call_count == 2


class TestNoRetryOnClientErrors:
    """4xx errors (except 429) should raise immediately without retry."""

    def test_404_raises_immediately(self) -> None:
        exc = openstack.exceptions.HttpException(message="Not Found")
        exc.status_code = 404
        mock = MagicMock(side_effect=exc)
        with pytest.raises(openstack.exceptions.HttpException):
            _sample_func(mock)
        assert mock.call_count == 1

    def test_400_raises_immediately(self) -> None:
        exc = openstack.exceptions.HttpException(message="Bad Request")
        exc.status_code = 400
        mock = MagicMock(side_effect=exc)
        with pytest.raises(openstack.exceptions.HttpException):
            _sample_func(mock)
        assert mock.call_count == 1

    def test_409_raises_immediately(self) -> None:
        exc = openstack.exceptions.HttpException(message="Conflict")
        exc.status_code = 409
        mock = MagicMock(side_effect=exc)
        with pytest.raises(openstack.exceptions.HttpException):
            _sample_func(mock)
        assert mock.call_count == 1


class TestRetryOnConnectionErrors:
    """Connection-level exceptions should trigger retries."""

    @patch("tenacity.nap.time")
    def test_retries_on_sdk_exception(self, mock_sleep: MagicMock) -> None:
        exc = openstack.exceptions.SDKException(message="SDK error")
        mock = MagicMock(side_effect=[exc, "ok"])
        assert _sample_func(mock) == "ok"
        assert mock.call_count == 2

    @patch("tenacity.nap.time")
    def test_retries_on_requests_connection_error(self, mock_sleep: MagicMock) -> None:
        exc = requests.exceptions.ConnectionError("connection reset")
        mock = MagicMock(side_effect=[exc, "ok"])
        assert _sample_func(mock) == "ok"
        assert mock.call_count == 2

    @patch("tenacity.nap.time")
    def test_retries_on_stdlib_connection_error(self, mock_sleep: MagicMock) -> None:
        exc = ConnectionError("connection refused")
        mock = MagicMock(side_effect=[exc, "ok"])
        assert _sample_func(mock) == "ok"
        assert mock.call_count == 2


class TestRetryExhaustion:
    """When all retries are exhausted, the original exception is re-raised."""

    @patch("tenacity.nap.time")
    def test_reraises_after_max_attempts(self, mock_sleep: MagicMock) -> None:
        exc = openstack.exceptions.HttpException(message="Service Unavailable")
        exc.status_code = 503
        mock = MagicMock(side_effect=exc)

        @retry(max_attempts=3)
        def limited_func(m: MagicMock) -> str:
            return m()

        with pytest.raises(openstack.exceptions.HttpException, match="Service Unavailable"):
            limited_func(mock)
        assert mock.call_count == 3


class TestRetryLogging:
    """Verify the log output format matches the expected pattern."""

    @patch("tenacity.nap.time")
    def test_logs_retry_warning(
        self, mock_sleep: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        exc = openstack.exceptions.HttpException(message="Service Unavailable")
        exc.status_code = 503
        mock = MagicMock(side_effect=[exc, "ok"])

        with caplog.at_level(logging.WARNING, logger="src.utils"):
            _sample_func(mock)

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert "Retry 1/5" in record.message
        assert "_sample_func" in record.message
        assert "HTTP 503" in record.message
        assert "sleeping" in record.message
