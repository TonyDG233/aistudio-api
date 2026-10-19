"""Remote image fetching with SSRF guards.

The upstream only accepts inline base64 data, so http(s) image URLs are
downloaded by the proxy and converted. Keep the guards in this module rather
than trusting client-provided URLs.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from aistudio_api.application.format_converters.common import (
    ConversionError,
    decode_data_url,
)

_DEFAULT_TIMEOUT_SECONDS = 15.0
_DEFAULT_MAX_BYTES = 20 * 1024 * 1024
_DEFAULT_MAX_REDIRECTS = 3


class MediaFetcher:
    """Download remote images and return ``data:`` URLs.

    DNS is resolved and every resolved address is validated before the
    request. Redirects are followed manually and re-validated. This is not a
    defense against a determined DNS-rebinding attacker; it is a guard against
    accidental loopback / metadata / private-network access.
    """

    def __init__(
        self,
        *,
        allow_private: bool = False,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        max_redirects: int = _DEFAULT_MAX_REDIRECTS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.allow_private = allow_private
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self._transport = transport

    async def to_data_url(self, url: str, *, field: str = "image_url") -> str:
        if url.startswith("data:"):
            mime_type, payload = decode_data_url(url, field=field)
            return f"data:{mime_type};base64,{payload}"

        current = url
        headers = {"User-Agent": "aistudio-api/1.0", "Accept": "image/*"}
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                for redirect_index in range(self.max_redirects + 1):
                    await self._validate_url(current, field=field)
                    async with client.stream(
                        "GET", current, headers=headers
                    ) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise ConversionError(
                                    "image URL redirected without a Location header",
                                    field=field,
                                )
                            if redirect_index >= self.max_redirects:
                                raise ConversionError(
                                    f"image URL exceeded {self.max_redirects} redirects",
                                    field=field,
                                )
                            current = urljoin(current, location)
                            continue
                        if response.status_code >= 400:
                            raise ConversionError(
                                f"failed to fetch image: HTTP {response.status_code}",
                                field=field,
                            )
                        content_type = response.headers.get("content-type", "")
                        mime_type = content_type.split(";", 1)[0].strip().lower()
                        if not mime_type.startswith("image/"):
                            raise ConversionError(
                                "remote URL did not return an image "
                                f"(Content-Type: {content_type or 'missing'})",
                                field=field,
                            )
                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > self.max_bytes:
                                raise ConversionError(
                                    f"remote image exceeds {self.max_bytes} bytes",
                                    field=field,
                                )
                            chunks.append(chunk)
                        if total == 0:
                            raise ConversionError("remote image is empty", field=field)
                        payload = base64.b64encode(b"".join(chunks)).decode("ascii")
                        return f"data:{mime_type};base64,{payload}"
        except ConversionError:
            raise
        except httpx.TimeoutException as exc:
            raise ConversionError(
                "timed out while fetching image URL", field=field
            ) from exc
        except httpx.HTTPError as exc:
            raise ConversionError(f"failed to fetch image: {exc}", field=field) from exc
        except OSError as exc:
            raise ConversionError(
                f"failed to resolve image host: {exc}", field=field
            ) from exc

        raise ConversionError("image URL redirect loop", field=field)

    async def _validate_url(self, url: str, *, field: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise ConversionError("image URL must use http or https", field=field)
        host = parts.hostname
        if not host:
            raise ConversionError("image URL has no host", field=field)
        addresses = await self._resolve(host, field=field)
        for address in addresses:
            if not self._address_allowed(address):
                raise ConversionError(
                    f"image URL host is not allowed: {host}", field=field
                )

    async def _resolve(
        self, host: str, *, field: str
    ) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            return {literal}
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ConversionError(
                f"failed to resolve image host: {host}", field=field
            ) from exc
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        for info in infos:
            raw = info[4][0]
            try:
                addresses.add(ipaddress.ip_address(raw))
            except ValueError:
                continue
        if not addresses:
            raise ConversionError(f"failed to resolve image host: {host}", field=field)
        return addresses

    def _address_allowed(
        self, address: ipaddress.IPv4Address | ipaddress.IPv6Address
    ) -> bool:
        # Cloud metadata / link-local is never allowed, even in permissive mode.
        if (
            address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        ):
            return False
        if address.is_loopback or address.is_private:
            return self.allow_private
        return True
