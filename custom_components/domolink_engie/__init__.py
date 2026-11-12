"""DomoLink ENGIE Électricité & Gaz integration setup."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ID_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    DOMAIN,
    PLATFORMS,
    SERVICE_IMPORT_HISTORY,
)
from .coordinator import EngieDataUpdateCoordinator
from .engie_api import EngieApiClient, create_engie_aiohttp_session

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DomoLink ENGIE from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = create_engie_aiohttp_session()
    client = EngieApiClient(
        session=session,
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        id_token=entry.data.get(CONF_ID_TOKEN),
        token_expires_at=float(entry.data.get(CONF_TOKEN_EXPIRES_AT, 0.0)),
    )

    coordinator = EngieDataUpdateCoordinator(hass, entry, client)
    client._on_token_refresh = coordinator.async_persist_tokens

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await session.close()
        raise

    hass.data[DOMAIN][entry.entry_id] = coordinator
    hass.data[DOMAIN][f"{entry.entry_id}_session"] = session
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_IMPORT_HISTORY):
        async def _handle_import_history(call: ServiceCall) -> None:
            months = int(call.data.get("months", 12))
            for coord in hass.data.get(DOMAIN, {}).values():
                if isinstance(coord, EngieDataUpdateCoordinator):
                    await coord.async_import_full_history(months=months)

        hass.services.async_register(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            _handle_import_history,
            schema=vol.Schema({vol.Optional("months", default=12): vol.All(int, vol.Range(min=1, max=36))}),
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a DomoLink ENGIE config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        session = hass.data[DOMAIN].pop(f"{entry.entry_id}_session", None)
        if session is not None:
            await session.close()
        has_coords = any(
            isinstance(v, EngieDataUpdateCoordinator)
            for v in hass.data.get(DOMAIN, {}).values()
        )
        if not has_coords and hass.services.has_service(DOMAIN, SERVICE_IMPORT_HISTORY):
            hass.services.async_remove(DOMAIN, SERVICE_IMPORT_HISTORY)
    return unload_ok
