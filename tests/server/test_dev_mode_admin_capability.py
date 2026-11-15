# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Dev mode must be admin-capable without any client-supplied API key.

Two behaviours are pinned here:

1. ``DevAuthPlugin.initialize`` builds an ``APIKeyManager`` so the Admin API gate
   (``get_api_key_manager_or_raise`` / ``require_auth_role``) no longer refuses with
   ``PermissionDeniedError`` — while identity still resolves to ROOT without a credential.
2. ``has_implicit_manage`` grants implicit ACL manage to ROOT as well as ADMIN.

Note on (2): this is a consistency fix, not the fix that unblocked ACL. ROOT already
short-circuits ACL resolution in ``viking_fs/_access.py`` (``_system_permission_allows``
and ``_is_accessible`` both return True for ``Role.ROOT``), and the ACL-manage check runs
after the target-exists check. Keeping the two role sources of truth aligned prevents the
ACL module from disagreeing with the access layer for a ROOT identity.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openviking.server.auth import (
    _build_request_context,
    get_api_key_manager_or_raise,
)
from openviking.server.auth.plugins import DevAuthPlugin
from openviking.server.config import ServerConfig
from openviking.server.identity import RequestContext, ResolvedIdentity, Role
from openviking.storage.acl import has_implicit_manage
from openviking_cli.session.user_id import UserIdentifier
from tests.server.account_store_fakes import InMemoryAGFS


class _FakeRequest:
    """Starlette-shaped request carrying app state, enough for these helpers."""

    def __init__(self, app) -> None:
        self.app = app
        self.state = SimpleNamespace()
        self.url = SimpleNamespace(path="/api/v1/admin/accounts")


@pytest.fixture
def dev_plugin() -> DevAuthPlugin:
    return DevAuthPlugin()


@pytest.fixture
def dev_app(dev_plugin: DevAuthPlugin):
    return SimpleNamespace(
        state=SimpleNamespace(
            auth_plugin=dev_plugin,
            api_key_manager=None,
            service_name="test-service",
        )
    )


@pytest.fixture
def fake_service():
    return SimpleNamespace(viking_fs=SimpleNamespace(agfs=InMemoryAGFS()))


async def test_dev_initialize_builds_api_key_manager(dev_app, dev_plugin, fake_service):
    """Dev mode's initialize must publish a real manager, not None."""
    await dev_plugin.initialize(dev_app, fake_service, ServerConfig())

    manager = dev_app.state.api_key_manager
    assert manager is not None
    assert getattr(dev_plugin, "_api_key_manager", None) is manager

    # The Admin API facade is now reachable instead of raising PermissionDeniedError.
    assert get_api_key_manager_or_raise(_FakeRequest(dev_app)) is manager

    await dev_plugin.shutdown()
    assert dev_app.state.api_key_manager is manager  # plugin.close does not re-null app state
    assert getattr(dev_plugin, "_api_key_manager", None) is None


async def test_dev_manager_has_no_root_key_and_never_authenticates(
    dev_app, dev_plugin, fake_service
):
    """A None root key must not crash, and no opaque key may resolve to ROOT."""
    await dev_plugin.initialize(dev_app, fake_service, ServerConfig())
    manager = dev_app.state.api_key_manager
    try:
        assert manager._root_api_key is None

        from openviking_cli.exceptions import UnauthenticatedError

        with pytest.raises(UnauthenticatedError):
            await manager.resolve_identity("default.default.deadbeef")
    finally:
        await dev_plugin.shutdown()


async def test_dev_identity_still_root_default_without_credential(
    dev_app, dev_plugin, fake_service
):
    """Dev mode keeps resolving ROOT/default/default with zero credentials."""
    await dev_plugin.initialize(dev_app, fake_service, ServerConfig())
    try:
        identity = await dev_plugin.resolve_identity(_FakeRequest(dev_app))
        assert identity.role == Role.ROOT
        assert identity.account_id == "default"
        assert identity.user_id == "default"

        # _uses_local_account_store is False for dev, so group/deletion lookups are
        # skipped even though a manager now exists: the code path must not crash.
        ctx = await _build_request_context(_FakeRequest(dev_app), identity)
        assert isinstance(ctx, RequestContext)
        assert ctx.role == Role.ROOT
        assert ctx.account_id == "default"
        assert ctx.user.user_id == "default"
        assert ctx.group_ids == ()
    finally:
        await dev_plugin.shutdown()


def test_dev_plugin_registration_and_bot_proxy_unchanged(dev_plugin: DevAuthPlugin):
    assert dev_plugin.auth_mode == "dev"
    assert dev_plugin.can_skip_api_key_for_bot_proxy() is True


# ---- has_implicit_manage ----


def _ctx(role: Role) -> RequestContext:
    return RequestContext(user=UserIdentifier("default", "default"), role=role)


@pytest.mark.parametrize("role", [Role.ADMIN, Role.ROOT])
def test_has_implicit_manage_for_admin_and_root(role: Role):
    assert has_implicit_manage(_ctx(role), "viking://resources/team/doc.md") is True


@pytest.mark.parametrize("role", [Role.USER, Role.GLOBAL])
def test_has_implicit_manage_is_false_for_other_roles(role: Role):
    assert has_implicit_manage(_ctx(role), "viking://resources/team/doc.md") is False


@pytest.mark.parametrize("role", [Role.ADMIN, Role.ROOT, Role.USER])
def test_has_implicit_manage_is_scoped_to_acl_namespace(role: Role):
    """Implicit manage only ever applies to viking://resources."""
    assert has_implicit_manage(_ctx(role), "viking://user/default/memories/x.md") is False
    assert has_implicit_manage(_ctx(role), "viking://temp/scratch") is False


def test_has_implicit_manage_normalizes_string_role():
    """RequestContext.__post_init__ parses the role; string input must match."""
    assert RequestContext(user=UserIdentifier("default", "default"), role="root")
    assert (
        has_implicit_manage(
            RequestContext(user=UserIdentifier("default", "default"), role="root"),
            "viking://resources/team/doc.md",
        )
        is True
    )
    assert (
        has_implicit_manage(
            RequestContext(user=UserIdentifier("default", "default"), role="user"),
            "viking://resources/team/doc.md",
        )
        is False
    )


def test_dev_identity_role_is_root_for_acl_manage():
    """The identity dev mode hands out satisfies has_implicit_manage."""
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")
    ctx = RequestContext(
        user=UserIdentifier(identity.account_id, identity.user_id), role=identity.role
    )
    assert has_implicit_manage(ctx, "viking://resources/anything") is True
