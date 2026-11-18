# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Dev mode authentication plugin."""

from __future__ import annotations

import sys
from typing import Optional

from fastapi import Request

from openviking.server.auth.plugin import AuthPlugin
from openviking.server.identity import ResolvedIdentity, Role
from openviking.server.store_assembly import build_api_key_manager

_LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_localhost(host: str) -> bool:
    """Return True if *host* resolves to a loopback address."""
    return host in _LOCALHOST_HOSTS


class DevAuthPlugin(AuthPlugin):
    """Development mode: no authentication, always ROOT.

    Only allowed when the server binds to localhost.
    """

    auth_mode = "dev"

    async def resolve_identity(
        self,
        request: Request,
        *,
        api_key: Optional[str] = None,
        x_openviking_account: Optional[str] = None,
        x_openviking_user: Optional[str] = None,
    ) -> ResolvedIdentity:
        """Dev mode: no authentication, always return ROOT."""
        return ResolvedIdentity(
            role=Role.ROOT,
            account_id=x_openviking_account or "default",
            user_id=x_openviking_user or "default",
        )

    def validate_config(self, config) -> None:
        """Dev mode is only allowed on localhost."""
        if _is_localhost(config.host):
            return
        print(
            "SECURITY: server.auth_mode='dev' requires server.host to be localhost, "
            "but a non-localhost host is configured. Dev mode exposes an unauthenticated ROOT "
            "endpoint and must not be exposed to the network.",
            file=sys.stderr,
            flush=True,
        )
        print(
            'Set server.auth_mode="api_key" and configure server.root_api_key, '
            'or bind dev mode to localhost (server.host = "127.0.0.1").',
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)

    async def initialize(self, app, service, config) -> None:
        """Dev mode builds the account store so the Admin API is usable locally.

        Dev mode has no root key: identity always resolves to ROOT / account
        ``default`` / user ``default`` without a credential. The manager exists only
        to satisfy the Admin API's account-store facade; it never authenticates a request.
        """
        manager = build_api_key_manager(
            root_key=None,
            viking_fs=service.viking_fs,
            api_key_hashing_enabled=config.api_key_hashing_enabled,
            service_name=app.state.service_name,
            account_store_provider=config.account_store.provider,
            account_store_params=config.account_store.params,
            account_store_watch_enabled=config.api_key_watch_enabled,
            account_store_watch_interval_seconds=config.api_key_watch_interval_seconds,
            issue_user_keys=False,
        )
        await manager.load()
        self._api_key_manager = manager
        app.state.api_key_manager = manager

    async def shutdown(self) -> None:
        manager = getattr(self, "_api_key_manager", None)
        try:
            if manager is not None:
                await manager.close()
        finally:
            self._api_key_manager = None

    def can_skip_api_key_for_bot_proxy(self) -> bool:
        return True
