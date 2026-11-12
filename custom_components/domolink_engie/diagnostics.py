"""Diagnostics support for DomoLink ENGIE Électricité & Gaz."""
from __future__ import annotations

from typing import Any

try:
    from homeassistant.components.diagnostics import async_redact_data
except ImportError:
    def async_redact_data(data: Any, to_redact: set[str]) -> Any:
        if isinstance(data, dict):
            return {
                k: ("**REDACTED**" if k in to_redact else async_redact_data(v, to_redact))
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [async_redact_data(item, to_redact) for item in data]
        return data

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ID_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DOMAIN,
)

TO_REDACT = {
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_ID_TOKEN,
    CONF_USERNAME,
    "password",
    "email",
    "nom",
    "prenom",
    "adresse_complete",
    "idPDL",
    "numeroSerie",
}


async def async_get_config_entry_diagnostics(
    hass: Any, entry: Any
) -> dict[str, Any]:
    """Return diagnostics for a config entry with strict PII/token redaction."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "summary": async_redact_data(data.get("summary") or {}, TO_REDACT),
        "fiabilite_data": data.get("fiabilite_data"),
        "last_sync_utc": data.get("last_sync_utc"),
        "elec_daily_count": len(data.get("elec_daily") or []),
        "gaz_daily_count": len(data.get("gaz_daily") or []),
    }
