"""Tests to verify state_store is None in dry-run modes for defense in depth."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.client import TenantCtl
from src.models.defaults import DefaultsConfig


@pytest.fixture
def mock_defaults() -> DefaultsConfig:
    """Return mock defaults configuration."""
    return DefaultsConfig()


@patch("src.client.TenantCtl._connect")
@patch("src.client.build_external_network_map")
def test_dry_run_offline_state_store_is_none(
    mock_build_map: MagicMock,
    mock_connect: MagicMock,
    mock_state_store: MagicMock,
    mock_defaults: DefaultsConfig,
) -> None:
    """Offline dry-run mode should set state_store to None for safety."""
    client = TenantCtl(config_dir="config/", state_store=mock_state_store, cloud="test-cloud")
    ctx, owns_connection = client._setup_context(
        mock_defaults,
        [],
        dry_run=True,
        offline=True,
    )

    # Verify state_store is None (defense in depth)
    assert ctx.state_store is None
    assert ctx.dry_run is True
    assert ctx.conn is None
    assert owns_connection is False

    # Verify no connection was attempted
    mock_connect.assert_not_called()
    mock_build_map.assert_not_called()


@patch("src.client.TenantCtl._connect")
@patch("src.client.build_external_network_map")
@patch("src.client.resolve_default_external_network")
@patch("src.client.resolve_federation_context")
def test_dry_run_online_state_store_is_none(
    mock_resolve_federation: MagicMock,
    mock_resolve_default: MagicMock,
    mock_build_map: MagicMock,
    mock_connect: MagicMock,
    mock_state_store: MagicMock,
    mock_defaults: DefaultsConfig,
) -> None:
    """Online dry-run mode should set state_store to None for safety."""
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn
    net_map = {"public": "ext-net-123", "ext-net-123": "ext-net-123"}
    mock_build_map.return_value = net_map
    mock_resolve_default.return_value = "ext-net-123"
    mock_resolve_federation.return_value = ([], False, [])

    client = TenantCtl(config_dir="config/", state_store=mock_state_store, cloud="test-cloud")
    ctx, owns_connection = client._setup_context(
        mock_defaults,
        [],
        dry_run=True,
        offline=False,
    )

    # Verify state_store is None (defense in depth)
    assert ctx.state_store is None
    assert ctx.dry_run is True
    assert ctx.conn is mock_conn
    assert ctx.external_net_id == "ext-net-123"
    assert owns_connection is True

    # Verify connection was established
    mock_connect.assert_called_once()
    mock_build_map.assert_called_once()


@patch("src.client.TenantCtl._connect")
@patch("src.client.build_external_network_map")
@patch("src.client.resolve_default_external_network")
@patch("src.client.resolve_federation_context")
def test_normal_mode_state_store_is_provided(
    mock_resolve_federation: MagicMock,
    mock_resolve_default: MagicMock,
    mock_build_map: MagicMock,
    mock_connect: MagicMock,
    mock_state_store: MagicMock,
    mock_defaults: DefaultsConfig,
) -> None:
    """Normal (non-dry-run) mode should preserve state_store."""
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn
    net_map = {"public": "ext-net-456", "ext-net-456": "ext-net-456"}
    mock_build_map.return_value = net_map
    mock_resolve_default.return_value = "ext-net-456"
    mock_resolve_federation.return_value = ([], False, [])

    client = TenantCtl(config_dir="config/", state_store=mock_state_store, cloud="test-cloud")
    ctx, owns_connection = client._setup_context(
        mock_defaults,
        [],
        dry_run=False,
        offline=False,
    )

    # Verify state_store is preserved in normal mode
    assert ctx.state_store is mock_state_store
    assert ctx.dry_run is False
    assert ctx.conn is mock_conn
    assert ctx.external_net_id == "ext-net-456"
    assert owns_connection is True
