"""Binary Sensor platform for DomoLink ENGIE Électricité & Gaz."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EngieDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class EngieBinarySensorDescription(BinarySensorEntityDescription):
    """Describe an ENGIE binary sensor."""

    value_fn: Callable[[dict[str, Any]], bool]
    condition_fn: Callable[[dict[str, Any]], bool] | None = None


BINARY_DESCRIPTIONS: tuple[EngieBinarySensorDescription, ...] = (
    EngieBinarySensorDescription(
        key="elec_communicant",
        name="Compteur Linky Communicant",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_elec")),
        value_fn=lambda d: bool((d.get("elec_releve") or {}).get("compteurCommuniquant")),
    ),
    EngieBinarySensorDescription(
        key="gaz_communicant",
        name="Compteur Gazpar Communicant",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_gaz")),
        value_fn=lambda d: bool((d.get("gaz_releve") or {}).get("compteurCommuniquant")),
    ),
    EngieBinarySensorDescription(
        key="erl_actif",
        name="Passerelle ERL Active",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool((d.get("summary") or {}).get("client_erl")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ENGIE binary sensors."""
    coordinator: EngieDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}

    entities = [
        EngieBinarySensor(coordinator, desc)
        for desc in BINARY_DESCRIPTIONS
        if desc.condition_fn is None or desc.condition_fn(data)
    ]
    async_add_entities(entities)


class EngieBinarySensor(CoordinatorEntity[EngieDataUpdateCoordinator], BinarySensorEntity):
    """Representation of an ENGIE diagnostic binary sensor."""

    entity_description: EngieBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EngieDataUpdateCoordinator,
        description: EngieBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        cc = coordinator.compte_contrat
        self._attr_unique_id = f"engie_{cc}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{cc}_account")},
            name=f"ENGIE Compte ({cc})",
            manufacturer="ENGIE",
            model="Contrat Particuliers",
        )

    @property
    def is_on(self) -> bool | None:
        """Return True when condition is active."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
