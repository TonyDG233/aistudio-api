from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.domain.errors import RequestError
from aistudio_api.infrastructure.gateway.client import AIStudioClient


@pytest.mark.asyncio
async def test_build_auth_uses_lease_not_global_session() -> None:
    client = AIStudioClient.__new__(AIStudioClient)
    client._session = cast(
        Any,
        SimpleNamespace(
            active_context=SimpleNamespace(
                auth_file="global-pg.json",
                auth_user="9",
            )
        ),
    )
    lease = SimpleNamespace(
        account_id="build-account",
        adapter=SimpleNamespace(auth_file="build-lease.json", auth_user="3"),
    )

    assert await client._build_lease_auth(lease) == ("build-lease.json", "3")


@pytest.mark.asyncio
async def test_build_lease_without_auth_file_fails_closed() -> None:
    client = AIStudioClient.__new__(AIStudioClient)
    lease = SimpleNamespace(
        account_id="build-account",
        adapter=SimpleNamespace(auth_file=None, auth_user="0"),
    )

    with pytest.raises(RequestError) as exc:
        await client._build_lease_auth(lease)
    assert exc.value.status == 503
