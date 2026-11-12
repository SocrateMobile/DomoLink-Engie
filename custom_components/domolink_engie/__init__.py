"""DomoLink ENGIE Électricité & Gaz integration setup."""
from __future__ import annotations

import logging
import os
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CARD_FILE_NAME,
    CONF_ACCESS_TOKEN,
    CONF_ID_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    DOMAIN,
    FRONTEND_URL_PATH,
    PANEL_FILE_NAME,
    PANEL_ICON,
    PANEL_NAME,
    PANEL_TITLE,
    PANEL_URL_PATH,
    PLATFORMS,
    SERVICE_IMPORT_HISTORY,
    VERSION,
)
from .coordinator import EngieDataUpdateCoordinator
from .engie_api import EngieApiClient, create_engie_aiohttp_session

_LOGGER = logging.getLogger(__name__)


def _build_account_snapshot(
    hass: HomeAssistant,
    entry_id: str,
    coord: EngieDataUpdateCoordinator,
) -> dict[str, Any]:
    """Build a JSON-serializable snapshot of one ENGIE ConfigEntry for the panel."""
    data = coord.data or {}
    summary = data.get("summary") or {}
    cc = coord.compte_contrat or summary.get("compte_contrat") or ""

    entities_map: dict[str, str] = {}
    try:
        from homeassistant.helpers import entity_registry as er

        ent_reg = er.async_get(hass)
        prefix = f"engie_{cc}_"
        for ent in er.async_entries_for_config_entry(ent_reg, entry_id):
            uid = ent.unique_id or ""
            key = uid[len(prefix):] if uid.startswith(prefix) else uid
            entities_map[key] = ent.entity_id
    except Exception:
        pass

    billing = data.get("billing") or {}
    return {
        "entry_id": entry_id,
        "compte_contrat": cc,
        "ville": summary.get("ville") or "",
        "code_postal": summary.get("code_postal") or "",
        "type_tarif": summary.get("type_tarif") or "",
        "has_elec": bool(summary.get("has_elec")),
        "has_gaz": bool(summary.get("has_gaz")),
        "client_erl": bool(summary.get("client_erl")),
        "elec_contract": summary.get("elec_contract") or {},
        "gaz_contract": summary.get("gaz_contract") or {},
        "elec_releve": data.get("elec_releve") or {},
        "gaz_releve": data.get("gaz_releve") or {},
        "elec_variation": data.get("elec_variation") or {},
        "gaz_variation": data.get("gaz_variation") or {},
        "billing": {
            "solde": billing.get("solde") or {},
            "plan_mens": billing.get("plan_mens") or {},
            "factures": billing.get("factures") or {},
            "paiements": billing.get("paiements") or {},
            "engagement": billing.get("engagement") or {},
            "repartition_usages": billing.get("repartition_usages") or {},
            "services_options": billing.get("services_options") or {},
            "cheque_energie": billing.get("cheque_energie") or {},
        },
        "erl": data.get("erl") or {},
        "last_sync_utc": data.get("last_sync_utc") or "",
        "entities": entities_map,
    }


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Register static frontend assets, sidebar panel (all users), and custom Lovelace card."""
    if hass.data.get(f"{DOMAIN}_frontend_registered"):
        return

    frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
    if not os.path.isdir(frontend_dir):
        return

    try:
        from homeassistant.components import frontend
        from homeassistant.components.http import StaticPathConfig
    except ImportError:
        return

    try:
        if hasattr(hass.http, "async_register_static_paths"):
            await hass.http.async_register_static_paths(
                [StaticPathConfig(FRONTEND_URL_PATH, frontend_dir, cache_headers=False)]
            )
        elif hasattr(hass.http, "register_static_path"):
            hass.http.register_static_path(FRONTEND_URL_PATH, frontend_dir, cache_headers=False)

        frontend.async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            frontend_url_path=PANEL_URL_PATH,
            config={
                "_panel_custom": {
                    "name": PANEL_NAME,
                    "module_url": f"{FRONTEND_URL_PATH}/{PANEL_FILE_NAME}?v={VERSION}",
                }
            },
            require_admin=False,
            update=True,
        )

        if hasattr(frontend, "add_extra_js_url"):
            frontend.add_extra_js_url(
                hass, f"{FRONTEND_URL_PATH}/{CARD_FILE_NAME}?v={VERSION}"
            )

        hass.data[f"{DOMAIN}_frontend_registered"] = True
    except Exception as err:
        _LOGGER.debug("DomoLink ENGIE frontend registration notice: %s", err)


def _register_websocket_commands(hass: HomeAssistant) -> None:
    """Register WebSocket commands used by the DomoLink ENGIE sidebar panel."""
    if hass.data.get(f"{DOMAIN}_ws_registered"):
        return

    try:
        from homeassistant.components import websocket_api
    except ImportError:
        return

    def _iter_coordinators(target_entry_id: str | None = None) -> list[tuple[str, EngieDataUpdateCoordinator]]:
        items: list[tuple[str, EngieDataUpdateCoordinator]] = []
        for eid, val in hass.data.get(DOMAIN, {}).items():
            if isinstance(val, EngieDataUpdateCoordinator):
                if target_entry_id and eid != target_entry_id:
                    continue
                items.append((eid, val))
        return items

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/get_snapshot",
            vol.Optional("entry_id"): str,
        }
    )
    @websocket_api.async_response
    async def ws_get_snapshot(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        accounts = [
            _build_account_snapshot(hass, eid, coord)
            for eid, coord in _iter_coordinators(msg.get("entry_id"))
        ]
        connection.send_result(msg["id"], {"version": VERSION, "accounts": accounts})

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/refresh_now",
            vol.Optional("entry_id"): str,
        }
    )
    @websocket_api.async_response
    async def ws_refresh_now(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        refreshed = 0
        for _, coord in _iter_coordinators(msg.get("entry_id")):
            await coord.async_request_refresh()
            refreshed += 1
        connection.send_result(msg["id"], {"refreshed": refreshed})

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/import_history",
            vol.Optional("entry_id"): str,
            vol.Optional("months", default=12): vol.All(int, vol.Range(min=1, max=36)),
        }
    )
    @websocket_api.async_response
    async def ws_import_history(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        total_points = 0
        months = int(msg.get("months", 12))
        for _, coord in _iter_coordinators(msg.get("entry_id")):
            total_points += await coord.async_import_full_history(months=months)
        connection.send_result(msg["id"], {"imported_points": total_points, "months": months})

    websocket_api.async_register_command(hass, ws_get_snapshot)
    websocket_api.async_register_command(hass, ws_refresh_now)
    websocket_api.async_register_command(hass, ws_import_history)
    hass.data[f"{DOMAIN}_ws_registered"] = True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


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

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await _async_register_frontend(hass)
    _register_websocket_commands(hass)

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
        if not has_coords:
            if hass.services.has_service(DOMAIN, SERVICE_IMPORT_HISTORY):
                hass.services.async_remove(DOMAIN, SERVICE_IMPORT_HISTORY)
            try:
                from homeassistant.components import frontend

                frontend.async_remove_panel(hass, PANEL_URL_PATH)
                hass.data.pop(f"{DOMAIN}_frontend_registered", None)
            except Exception:
                pass
    return unload_ok
