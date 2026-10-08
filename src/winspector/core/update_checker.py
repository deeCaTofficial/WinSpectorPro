"""Проверка опубликованного релиза WinSpector Pro на GitHub."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from .. import APP_VERSION

LATEST_RELEASE_API = "https://api.github.com/repos/deeCaTofficial/WinSpectorPro/releases/latest"
LATEST_RELEASE_PAGE = "https://github.com/deeCaTofficial/WinSpectorPro/releases/latest"
_VERSION_PATTERN = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)$")


def _version_parts(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    return tuple(map(int, match.groups()))


@dataclass(frozen=True)
class UpdateInfo:
    installed_version: str
    latest_version: str | None = None
    error: str | None = None

    @property
    def available(self) -> bool:
        installed = _version_parts(self.installed_version)
        latest = _version_parts(self.latest_version or "")
        return installed is not None and latest is not None and latest > installed


async def check_latest_release(
    installed_version: str = APP_VERSION,
    *,
    client: httpx.AsyncClient | None = None,
) -> UpdateInfo:
    """Сравнивает установленную версию с последним стабильным релизом.

    Клиент можно передать снаружи для изолированной проверки. Реальный
    запрос выполняется асинхронно и ограничен таймаутом.
    """
    if _version_parts(installed_version) is None:
        return UpdateInfo(installed_version, error="Не удалось определить установленную версию.")

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=8.0) as owned_client:
                response = await _request_release(owned_client, installed_version)
        else:
            response = await _request_release(client, installed_version)
        response.raise_for_status()
        payload = response.json()
        tag = payload.get("tag_name") if isinstance(payload, dict) else None
        if not isinstance(tag, str) or _version_parts(tag) is None:
            return UpdateInfo(
                installed_version,
                error="GitHub вернул релиз без распознаваемой версии.",
            )
        return UpdateInfo(installed_version, latest_version=tag.removeprefix("v").removeprefix("V"))
    except (httpx.HTTPError, ValueError, TypeError):
        return UpdateInfo(installed_version, error="Не удалось проверить обновления на GitHub.")


async def _request_release(client: httpx.AsyncClient, installed_version: str) -> httpx.Response:
    return await client.get(
        LATEST_RELEASE_API,
        timeout=8.0,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"WinSpectorPro/{installed_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
