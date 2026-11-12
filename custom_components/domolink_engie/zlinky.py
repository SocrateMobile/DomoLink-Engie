"""Auto-detection and real-time state resolution for ZLinky_TIC (Z2M, ZHA, deCONZ/Dresden, TIC)."""
from __future__ import annotations

import re
from typing import Any

from .const import (
    CONF_ZLINKY_CURRENT_SENSOR,
    CONF_ZLINKY_DAILY_ENERGY_SENSOR,
    CONF_ZLINKY_DEMAIN_SENSOR,
    CONF_ZLINKY_DEVICE_PREFIX,
    CONF_ZLINKY_ENABLED,
    CONF_ZLINKY_INDEX_HC_SENSOR,
    CONF_ZLINKY_INDEX_HP_SENSOR,
    CONF_ZLINKY_INDEX_TOTAL_SENSOR,
    CONF_ZLINKY_MAX_POWER_SENSOR,
    CONF_ZLINKY_POWER_SENSOR,
    CONF_ZLINKY_PREF_SENSOR,
    CONF_ZLINKY_PTEC_SENSOR,
    CONF_ZLINKY_VOLTAGE_SENSOR,
)
from .engie_api import safe_float

# Ordered specification of ZLinky_TIC sensor slots for Z2M, ZHA, deCONZ/Dresden, ESPHome TIC
ZLINKY_SLOTS: tuple[dict[str, Any], ...] = (
    {
        "key": CONF_ZLINKY_POWER_SENSOR,
        "short": "power",
        "label": "Puissance apparente instantanée (SINSTS / PAPP)",
        "hint": "VA ou W — ex: sensor.zlinky_tic_sinsts, sensor.zlinky_apparent_power",
        "suffixes": (
            "linky_puissance",
            "zlinky_apparent_power",
            "sinsts",
            "papp",
            "apparent_power",
            "puissance_apparente",
            "electrical_measurement_apparent_power",
            "puissance_instantanee",
            "sinsts1",
            "rms_power",
            "active_power",
            "shelly_power",
        ),
        "units": ("VA", "W", "kVA", "kW"),
    },
    {
        "key": CONF_ZLINKY_CURRENT_SENSOR,
        "short": "current",
        "label": "Intensité efficace (IRMS1 / IINST)",
        "hint": "Ampères (A) — ex: sensor.zlinky_tic_irms1, sensor.zlinky_iinst, sensor.zlinky_current",
        "suffixes": (
            "irms1",
            "iinst",
            "rms_current",
            "courant_efficace",
            "electrical_measurement_rms_current",
            "current_phase_1",
            "intensite",
            "current",
            "courant",
        ),
        "units": ("A", "mA"),
    },
    {
        "key": CONF_ZLINKY_VOLTAGE_SENSOR,
        "short": "voltage",
        "label": "Tension efficace réseau (URMS1 / UMOY1)",
        "hint": "Volts (V) — ex: sensor.zlinky_tic_urms1, sensor.zlinky_voltage",
        "suffixes": (
            "urms1",
            "umoy1",
            "rms_voltage",
            "tension_efficace",
            "electrical_measurement_rms_voltage",
            "voltage_phase_1",
            "voltage",
            "tension",
        ),
        "units": ("V",),
    },
    {
        "key": CONF_ZLINKY_INDEX_TOTAL_SENSOR,
        "short": "index_total",
        "label": "Index Total Compteur (EAST / BASE)",
        "hint": "kWh ou Wh — ex: sensor.zlinky_tic_east, sensor.zlinky_base, sensor.zlinky_summation_delivered",
        "suffixes": (
            "east",
            "base",
            "summation_delivered",
            "smartenergy_metering_summation_delivered",
            "energie_totale_livree",
            "total_energy",
            "index_total",
            "energy",
        ),
        "units": ("kWh", "Wh"),
    },
    {
        "key": CONF_ZLINKY_INDEX_HC_SENSOR,
        "short": "index_hc",
        "label": "Index Heures Creuses (EASF01 / HCHC)",
        "hint": "kWh ou Wh — ex: sensor.zlinky_tic_easf01, sensor.zlinky_hchc, sensor.zlinky_tier_1_summation_delivered",
        "suffixes": (
            "easf01",
            "hchc",
            "tier_1_summation_delivered",
            "smartenergy_metering_tier_1_summation_delivered",
            "bbrhcjb",
            "ejphn",
            "index_hc",
            "heures_creuses",
        ),
        "units": ("kWh", "Wh"),
    },
    {
        "key": CONF_ZLINKY_INDEX_HP_SENSOR,
        "short": "index_hp",
        "label": "Index Heures Pleines (EASF02 / HCHP)",
        "hint": "kWh ou Wh — ex: sensor.zlinky_tic_easf02, sensor.zlinky_hchp, sensor.zlinky_tier_2_summation_delivered",
        "suffixes": (
            "easf02",
            "hchp",
            "tier_2_summation_delivered",
            "smartenergy_metering_tier_2_summation_delivered",
            "bbrhpjb",
            "ejpmp",
            "index_hp",
            "heures_pleines",
        ),
        "units": ("kWh", "Wh"),
    },
    {
        "key": CONF_ZLINKY_PTEC_SENSOR,
        "short": "ptec",
        "label": "Période tarifaire en cours (PTEC / LTARF)",
        "hint": "HP / HC / Tempo — ex: sensor.zlinky_tic_ptec, sensor.zlinky_ltarf, sensor.zlinky_active_register_tier_delivered",
        "suffixes": (
            "ptec",
            "ltarf",
            "active_register_tier_delivered",
            "current_tarif",
            "current_tier_delivered",
            "periode_tarifaire",
            "tarif_en_cours",
            "ntarf",
        ),
        "units": None,
    },
    {
        "key": CONF_ZLINKY_MAX_POWER_SENSOR,
        "short": "max_power",
        "label": "Puissance maximale du jour (SMAXSN / PMAX)",
        "hint": "VA ou W — ex: sensor.zlinky_tic_smaxsn, sensor.zlinky_pmax",
        "suffixes": (
            "smaxsn",
            "pmax",
            "smaxsn1",
            "max_power",
            "daily_max_power",
            "puissance_max",
            "imax",
        ),
        "units": ("VA", "W", "kVA", "kW", "A"),
    },
    {
        "key": CONF_ZLINKY_PREF_SENSOR,
        "short": "pref",
        "label": "Puissance / Intensité souscrite (PREF / ISOUSC)",
        "hint": "kVA ou A — ex: sensor.zlinky_tic_pref, sensor.zlinky_isousc, sensor.zlinky_available_power",
        "suffixes": (
            "pref",
            "isousc",
            "available_power",
            "power_threshold",
            "puissance_souscrite",
            "pcoup",
        ),
        "units": ("kVA", "VA", "A", "kW"),
    },
    {
        "key": CONF_ZLINKY_DAILY_ENERGY_SENSOR,
        "short": "daily_energy",
        "label": "Énergie quotidienne (Optionnel — sinon calculée via Index)",
        "hint": "kWh ou Wh — ex: sensor.zlinky_daily_energy, sensor.linky_energie_quotidienne",
        "suffixes": (
            "daily_energy",
            "energie_quotidienne",
            "energie_jour",
            "conso_jour",
            "today_energy",
            "energy_today",
        ),
        "units": ("kWh", "Wh"),
    },
    {
        "key": CONF_ZLINKY_DEMAIN_SENSOR,
        "short": "demain",
        "label": "Couleur Tempo Demain (DEMAIN / RTE Tempo)",
        "hint": "Optionnel — ex: sensor.zlinky_tic_demain, sensor.rte_tempo_prochaine_couleur",
        "suffixes": (
            "demain",
            "tomorrow_color",
            "rte_tempo_prochaine_couleur",
            "tempo_demain",
            "tempo_tomorrow",
        ),
        "units": None,
    },
)

# Characteristic TIC suffixes used to identify a ZLinky device prefix automatically
_TIC_SIGNATURE_SUFFIXES: tuple[str, ...] = (
    "_sinsts",
    "_papp",
    "_east",
    "_easf01",
    "_easf02",
    "_hchc",
    "_hchp",
    "_ptec",
    "_ltarf",
    "_urms1",
    "_irms1",
    "_iinst",
    "_smaxsn",
    "_isousc",
    "_pref",
    "_apparent_power",
    "_summation_delivered",
    "_tier_1_summation_delivered",
    "_tier_2_summation_delivered",
    "_active_register_tier_delivered",
)


def _iter_sensor_states(hass: Any) -> list[dict[str, Any]]:
    """Extract all non-ENGIE sensor states from Home Assistant state machine."""
    if hass is None or not hasattr(hass, "states") or hass.states is None:
        return []

    raw_states: list[Any] = []
    if hasattr(hass.states, "async_all"):
        try:
            raw_states = list(hass.states.async_all("sensor"))
        except TypeError:
            raw_states = [
                st for st in hass.states.async_all() if str(getattr(st, "entity_id", "")).startswith("sensor.")
            ]
    elif isinstance(hass.states, dict):
        for eid, st in hass.states.items():
            if str(eid).startswith("sensor."):
                raw_states.append((eid, st))

    results: list[dict[str, Any]] = []
    for item in raw_states:
        if isinstance(item, tuple):
            eid, st = item
            state_val = st.get("state") if isinstance(st, dict) else getattr(st, "state", None)
            attrs = st.get("attributes", {}) if isinstance(st, dict) else (getattr(st, "attributes", None) or {})
        else:
            eid = getattr(item, "entity_id", "")
            state_val = getattr(item, "state", None)
            attrs = getattr(item, "attributes", None) or {}

        if not eid or not eid.startswith("sensor.") or eid.startswith("sensor.engie_"):
            continue

        results.append(
            {
                "entity_id": eid,
                "object_id": eid[len("sensor."):],
                "state": str(state_val) if state_val is not None else "",
                "unit": str(attrs.get("unit_of_measurement") or ""),
                "friendly_name": str(attrs.get("friendly_name") or eid),
                "device_class": str(attrs.get("device_class") or ""),
            }
        )
    return results


_ZLINKY_TOKEN_RE = re.compile(r"(^|_)(zlinky|lixee|linky_tic|zlinky_tic|teleinfo|linky|tic)($|_)")


def _has_zlinky_token(text: str) -> bool:
    """Return True if text contains a whole ZLinky/Linky/TIC token (never matching 'atlantic' or 'diagnostic')."""
    return bool(_ZLINKY_TOKEN_RE.search(text.lower()))


def _extract_prefix_from_object_id(object_id: str) -> str | None:
    """Extract device prefix from a sensor object_id if it matches ZLinky/TIC naming."""
    oid = object_id.lower()
    for suf in _TIC_SIGNATURE_SUFFIXES:
        if oid.endswith(suf) and len(oid) > len(suf):
            return oid[: -len(suf)]
    if any(k in oid for k in ("zlinky", "lixee", "linky_tic", "zlinky_tic")):
        parts = oid.split("_")
        if len(parts) >= 2:
            return "_".join(parts[:-1])
    return None


def detect_zlinky_devices(sensors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group candidate ZLinky_TIC prefixes (Z2M, ZHA, deCONZ, TIC) by confidence score."""
    prefix_Lock: dict[str, list[str]] = {}
    for s in sensors:
        oid = s["object_id"]
        pref = _extract_prefix_from_object_id(oid)
        if pref:
            prefix_Lock.setdefault(pref, []).append(s["entity_id"])

    devices: list[dict[str, Any]] = []
    for pref, eids in prefix_Lock.items():
        is_explicit_zlinky = _has_zlinky_token(pref)
        # Require at least 2 TIC signature entities if prefix doesn't explicitly say zlinky/linky/tic
        if not is_explicit_zlinky and len(eids) < 2:
            continue
        score = len(eids) + (10 if is_explicit_zlinky else 0)
        label_clean = pref.replace("_", " ").upper()
        devices.append(
            {
                "prefix": pref,
                "label": f"{label_clean} ({len(eids)} capteurs détectés)",
                "count": len(eids),
                "score": score,
                "entities": sorted(eids),
            }
        )

    devices.sort(key=lambda d: (-d["score"], d["prefix"]))
    return devices


def _match_slot_entity(
    slot: dict[str, Any],
    sensors: list[dict[str, Any]],
    preferred_prefix: str = "",
) -> dict[str, Any] | None:
    """Find the best matching sensor for a given ZLinky_TIC slot."""
    slot_key = slot["key"]
    suffixes: tuple[str, ...] = slot["suffixes"]
    expected_units: tuple[str, ...] | None = slot["units"]

    candidates: list[tuple[int, dict[str, Any]]] = []
    for s in sensors:
        oid = s["object_id"].lower()
        unit = s["unit"]

        # Avoid matching max/min/injection/yesterday sensors on instantaneous power slot
        if slot_key == CONF_ZLINKY_POWER_SENSOR and any(
            bad in oid
            for bad in ("smax", "pmax", "max_power", "min_power", "sinsti", "injection", "hier", "yesterday", "moyen")
        ):
            continue

        # Check suffix or pattern match
        matched_rank = -1
        for idx, suf in enumerate(suffixes):
            if (
                oid == suf
                or oid.endswith(f"_{suf}")
                or (slot_key in (CONF_ZLINKY_POWER_SENSOR, CONF_ZLINKY_DAILY_ENERGY_SENSOR) and suf in oid)
                or (_has_zlinky_token(oid) and suf in oid)
            ):
                matched_rank = idx
                break

        # Fallback for power slot: any sensor with unit 'VA' (just like v1.2.0)
        if matched_rank < 0 and slot_key == CONF_ZLINKY_POWER_SENSOR and unit == "VA":
            matched_rank = len(suffixes)

        if matched_rank < 0:
            continue

        # Validate unit if slot enforces units and sensor reports a unit
        if expected_units is not None and unit and unit not in expected_units:
            continue

        score = 100 - matched_rank * 3
        if preferred_prefix and oid.startswith(preferred_prefix.lower()):
            score += 45
        elif any(k in oid for k in ("zlinky", "lixee")):
            score += 30
        elif _has_zlinky_token(oid):
            score += 18

        if s["state"] not in ("", "unknown", "unavailable", "None"):
            score += 10
            val = safe_float(s["state"])
            if slot_key == CONF_ZLINKY_POWER_SENSOR:
                if unit in ("VA", "kVA"):
                    score += 25
                if val is not None and val > 0:
                    score += 45
                if val is not None and val >= 50:
                    score += 25
            elif val is not None and val > 0:
                score += 15

        candidates.append((score, s))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]["entity_id"]))
    return candidates[0][1]


def _normalize_energy_kwh(val: float | None, unit: str) -> float | None:
    if val is None:
        return None
    if unit.strip().lower() == "wh":
        return round(val / 1000.0, 3)
    return round(val, 3)


def _normalize_power_va(val: float | None, unit: str) -> float | None:
    if val is None:
        return None
    u = unit.strip().lower()
    if u in ("kva", "kw"):
        return round(val * 1000.0, 1)
    return round(val, 1)


def _normalize_pref_kva(val: float | None, unit: str) -> float | None:
    if val is None or val <= 0:
        return None
    u = unit.strip().lower()
    if u == "a":
        # ISOUSC in Amps (e.g. 15A -> 3kVA, 30A -> 6kVA, 45A -> 9kVA, 60A -> 12kVA)
        return round(val * 0.2, 1)
    if u in ("va", "w"):
        return round(val / 1000.0, 1) if val >= 100 else round(val, 1)
    # Sometimes ZLinky PREF is reported in kVA without unit or with kVA unit (e.g., 6, 9, 12)
    if val >= 1000:
        return round(val / 1000.0, 1)
    return round(val, 1)


def _is_hc_from_ptec(ptec: str | None) -> bool | None:
    if not ptec:
        return None
    up = ptec.upper()
    if any(k in up for k in ("HC", "CREUSE", "TIER 1", "TIER_1", "HEURE CREUSE")):
        return True
    if any(k in up for k in ("HP", "PLEINE", "BASE", "NORMALE", "TIER 2", "TIER_2", "HEURE PLEINE")):
        return False
    return None


def detect_and_resolve_zlinky(
    hass: Any,
    options: dict[str, Any] | None = None,
    elec_releve: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Auto-detect ZLinky_TIC devices/entities, apply user overrides, and compute live telemetry."""
    opts = options or {}
    enabled = bool(opts.get(CONF_ZLINKY_ENABLED, True))
    sensors = _iter_sensor_states(hass)
    by_eid = {s["entity_id"]: s for s in sensors}

    detected_devices = detect_zlinky_devices(sensors)
    configured_prefix = str(opts.get(CONF_ZLINKY_DEVICE_PREFIX) or "").strip()
    active_prefix = configured_prefix or (detected_devices[0]["prefix"] if detected_devices else "")

    configured_map: dict[str, str] = {}
    resolved_map: dict[str, dict[str, Any]] = {}

    for slot in ZLINKY_SLOTS:
        key = slot["key"]
        manual_eid = str(opts.get(key) or "").strip()
        configured_map[key] = manual_eid

        chosen_sensor: dict[str, Any] | None = None
        is_manual = False

        if manual_eid:
            is_manual = True
            chosen_sensor = by_eid.get(manual_eid) or {
                "entity_id": manual_eid,
                "object_id": manual_eid.replace("sensor.", ""),
                "state": "unavailable",
                "unit": "",
                "friendly_name": manual_eid,
            }
        elif enabled:
            chosen_sensor = _match_slot_entity(slot, sensors, preferred_prefix=active_prefix)

        if chosen_sensor:
            resolved_map[key] = {
                "key": key,
                "short": slot["short"],
                "label": slot["label"],
                "entity_id": chosen_sensor["entity_id"],
                "state": chosen_sensor["state"],
                "value": safe_float(chosen_sensor["state"]),
                "unit": chosen_sensor["unit"],
                "friendly_name": chosen_sensor["friendly_name"],
                "is_manual": is_manual,
            }
        else:
            resolved_map[key] = {
                "key": key,
                "short": slot["short"],
                "label": slot["label"],
                "entity_id": "",
                "state": "",
                "value": None,
                "unit": "",
                "friendly_name": "",
                "is_manual": False,
            }

    # Extract normalized live telemetry when enabled
    def _slot_val(k: str) -> tuple[float | None, str, str]:
        info = resolved_map.get(k) or {}
        st = str(info.get("state") or "")
        if st in ("", "unknown", "unavailable", "None"):
            return None, str(info.get("unit") or ""), ""
        return info.get("value"), str(info.get("unit") or ""), st

    power_raw, power_unit, _ = _slot_val(CONF_ZLINKY_POWER_SENSOR)
    curr_raw, curr_unit, _ = _slot_val(CONF_ZLINKY_CURRENT_SENSOR)
    volt_raw, _, _ = _slot_val(CONF_ZLINKY_VOLTAGE_SENSOR)
    idx_tot_raw, idx_tot_unit, _ = _slot_val(CONF_ZLINKY_INDEX_TOTAL_SENSOR)
    idx_hc_raw, idx_hc_unit, _ = _slot_val(CONF_ZLINKY_INDEX_HC_SENSOR)
    idx_hp_raw, idx_hp_unit, _ = _slot_val(CONF_ZLINKY_INDEX_HP_SENSOR)
    _, _, ptec_str = _slot_val(CONF_ZLINKY_PTEC_SENSOR)
    max_p_raw, max_p_unit, _ = _slot_val(CONF_ZLINKY_MAX_POWER_SENSOR)
    pref_raw, pref_unit, _ = _slot_val(CONF_ZLINKY_PREF_SENSOR)
    daily_raw, daily_unit, _ = _slot_val(CONF_ZLINKY_DAILY_ENERGY_SENSOR)
    _, _, demain_str = _slot_val(CONF_ZLINKY_DEMAIN_SENSOR)

    power_va = _normalize_power_va(power_raw, power_unit) if enabled else None
    current_a = (round(curr_raw / 1000.0, 2) if curr_unit.lower() == "ma" else round(curr_raw, 2)) if (enabled and curr_raw is not None) else None
    voltage_v = round(volt_raw, 1) if (enabled and volt_raw is not None) else None
    index_hc_kwh = _normalize_energy_kwh(idx_hc_raw, idx_hc_unit) if enabled else None
    index_hp_kwh = _normalize_energy_kwh(idx_hp_raw, idx_hp_unit) if enabled else None
    index_total_kwh = _normalize_energy_kwh(idx_tot_raw, idx_tot_unit) if enabled else None
    if index_total_kwh is None and index_hc_kwh is not None and index_hp_kwh is not None:
        index_total_kwh = round(index_hc_kwh + index_hp_kwh, 3)

    max_power_va = _normalize_power_va(max_p_raw, max_p_unit) if enabled else None
    pref_kva = _normalize_pref_kva(pref_raw, pref_unit) if enabled else None
    ptec_clean = ptec_str.strip() if (enabled and ptec_str) else None
    is_hc = _is_hc_from_ptec(ptec_clean)

    # Compute real-time today consumption (kWh) and HC consumption (kWh)
    daily_energy_kwh = _normalize_energy_kwh(daily_raw, daily_unit) if enabled else None
    daily_hc_kwh: float | None = None
    daily_hp_kwh: float | None = None

    er = elec_releve or {}
    engie_last_idx = safe_float(er.get("dernierIndex"))
    engie_last_hc = safe_float(er.get("dernierIndexHC"))
    engie_last_hp = safe_float(er.get("dernierIndexHP"))

    if enabled:
        if index_hc_kwh is not None and engie_last_hc is not None:
            delta_hc = index_hc_kwh - engie_last_hc
            if 0.0 <= delta_hc <= 120.0:
                daily_hc_kwh = round(delta_hc, 2)
        if index_hp_kwh is not None and engie_last_hp is not None:
            delta_hp = index_hp_kwh - engie_last_hp
            if 0.0 <= delta_hp <= 120.0:
                daily_hp_kwh = round(delta_hp, 2)

        if daily_energy_kwh is None:
            if daily_hc_kwh is not None and daily_hp_kwh is not None:
                daily_energy_kwh = round(daily_hc_kwh + daily_hp_kwh, 2)
            elif index_total_kwh is not None and engie_last_idx is not None:
                delta_tot = index_total_kwh - engie_last_idx
                if 0.0 <= delta_tot <= 150.0:
                    daily_energy_kwh = round(delta_tot, 2)

    matched_count = sum(1 for v in resolved_map.values() if v.get("entity_id"))
    active_count = sum(
        1
        for v in resolved_map.values()
        if v.get("entity_id") and v.get("state") not in ("", "unknown", "unavailable", "None")
    )

    # Build a lightweight list of candidate sensors for UI autocomplete
    available_sensors = [
        {
            "entity_id": s["entity_id"],
            "friendly_name": s["friendly_name"],
            "state": s["state"],
            "unit": s["unit"],
            "is_zlinky": bool(_extract_prefix_from_object_id(s["object_id"]))
            or any(k in s["object_id"].lower() for k in ("zlinky", "lixee", "linky", "tic", "tempo")),
        }
        for s in sorted(sensors, key=lambda x: (0 if "zlinky" in x["entity_id"] or "linky" in x["entity_id"] else 1, x["entity_id"]))
    ]

    return {
        "enabled": enabled,
        "detected": matched_count > 0,
        "matched_count": matched_count,
        "active_count": active_count,
        "device_prefix": active_prefix,
        "configured_prefix": configured_prefix,
        "detected_devices": detected_devices,
        "slots": [
            {
                "key": slot["key"],
                "short": slot["short"],
                "label": slot["label"],
                "hint": slot["hint"],
                "configured_entity": configured_map.get(slot["key"], ""),
                **resolved_map[slot["key"]],
            }
            for slot in ZLINKY_SLOTS
        ],
        "configured": configured_map,
        "resolved": {k: v["entity_id"] for k, v in resolved_map.items() if v.get("entity_id")},
        "live": {
            "power_va": power_va,
            "current_a": current_a,
            "voltage_v": voltage_v,
            "index_total_kwh": index_total_kwh,
            "index_hc_kwh": index_hc_kwh,
            "index_hp_kwh": index_hp_kwh,
            "ptec": ptec_clean,
            "is_hc": is_hc,
            "max_power_va": max_power_va,
            "pref_kva": pref_kva,
            "daily_energy_kwh": daily_energy_kwh,
            "daily_hc_kwh": daily_hc_kwh,
            "daily_hp_kwh": daily_hp_kwh,
            "demain": demain_str.strip() if (enabled and demain_str) else None,
        },
        "available_sensors": available_sensors[:250],
    }
