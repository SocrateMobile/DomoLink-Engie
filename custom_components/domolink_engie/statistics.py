"""Retroactive Energy Dashboard statistics importer for DomoLink ENGIE."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from .const import DOMAIN
from .engie_api import safe_float

_LOGGER = logging.getLogger(__name__)

try:
    from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
    from homeassistant.components.recorder.statistics import async_add_external_statistics
    from homeassistant.core import HomeAssistant

    HAS_HA_RECORDER = True
except ImportError:
    HAS_HA_RECORDER = False
    StatisticData = None  # type: ignore[assignment,misc]
    StatisticMetaData = None  # type: ignore[assignment,misc]
    async_add_external_statistics = None  # type: ignore[assignment]
    HomeAssistant = Any  # type: ignore[assignment,misc]


def parse_engie_datetime_utc(raw_date: Any) -> datetime | None:
    """Parse ENGIE date strings and align strictly to top-of-hour UTC for HA recorder."""
    if not raw_date:
        return None
    s = str(raw_date).strip()

    dt: datetime | None = None
    # Remove trailing timezone offset variations if simple ISO parsing handles them
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        for fmt in (
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%m/%Y",
            "%Y-%m",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue

    if dt is None:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # HA recorder statistics require exact top-of-hour timestamps
    return dt.replace(minute=0, second=0, microsecond=0)


def build_cumulative_series(
    records: list[dict[str, Any]],
    value_key: str,
    date_keys: tuple[str, ...] = ("valeurDate", "dateDebut", "date", "datetime"),
) -> list[dict[str, Any]]:
    """Convert ENGIE period records into deduplicated monotonic cumulative points."""
    dated_values: dict[datetime, float] = {}

    for rec in records:
        if not isinstance(rec, dict):
            continue
        raw_val = safe_float(rec.get(value_key))
        if raw_val is None or raw_val < 0:
            continue

        dt_utc: datetime | None = None
        for dk in date_keys:
            if rec.get(dk):
                dt_utc = parse_engie_datetime_utc(rec[dk])
                if dt_utc is not None:
                    break
        if dt_utc is None:
            continue

        # Accumulate if multiple sub-records share the same top-of-hour slot
        dated_values[dt_utc] = dated_values.get(dt_utc, 0.0) + raw_val

    running_sum = 0.0
    series: list[dict[str, Any]] = []
    for dt_utc in sorted(dated_values.keys()):
        val = round(dated_values[dt_utc], 4)
        running_sum = round(running_sum + val, 4)
        series.append(
            {
                "start": dt_utc,
                "state": val,
                "sum": running_sum,
            }
        )
    return series


async def async_import_engie_statistics(
    hass: HomeAssistant,
    cc: str,
    elec_records: list[dict[str, Any]],
    gaz_records: list[dict[str, Any]],
) -> int:
    """Import historical electricity & gas statistics into Home Assistant Recorder."""
    if not HAS_HA_RECORDER or async_add_external_statistics is None:
        _LOGGER.debug("HA recorder not available, skipping external statistics import.")
        return 0

    imported_count = 0
    cc_clean = "".join(c for c in cc if c.isalnum()).lower()

    specs = [
        (
            f"{DOMAIN}:{cc_clean}_elec_kwh",
            f"ENGIE {cc} Historique Électricité (kWh)",
            "kWh",
            elec_records,
            "conso",
        ),
        (
            f"{DOMAIN}:{cc_clean}_elec_eur",
            f"ENGIE {cc} Coût Électricité (EUR)",
            "EUR",
            elec_records,
            "consoEuro",
        ),
        (
            f"{DOMAIN}:{cc_clean}_gaz_m3",
            f"ENGIE {cc} Historique Gaz (m³)",
            "m³",
            gaz_records,
            "volume",
        ),
        (
            f"{DOMAIN}:{cc_clean}_gaz_kwh",
            f"ENGIE {cc} Historique Gaz (kWh)",
            "kWh",
            gaz_records,
            "conso",
        ),
        (
            f"{DOMAIN}:{cc_clean}_gaz_eur",
            f"ENGIE {cc} Coût Gaz (EUR)",
            "EUR",
            gaz_records,
            "consoEuro",
        ),
    ]

    for stat_id, name, unit, records, key in specs:
        if not records:
            continue
        series = build_cumulative_series(records, key)
        if not series:
            continue

        meta_kwargs: dict[str, Any] = {
            "has_sum": True,
            "name": name,
            "source": DOMAIN,
            "statistic_id": stat_id,
            "unit_of_measurement": unit,
        }
        try:
            from homeassistant.components.recorder.models import StatisticMeanType

            meta_kwargs["mean_type"] = StatisticMeanType.NONE
        except ImportError:
            meta_kwargs["has_mean"] = False

        metadata = StatisticMetaData(**meta_kwargs)
        stat_data = [
            StatisticData(start=pt["start"], state=pt["state"], sum=pt["sum"])
            for pt in series
        ]
        async_add_external_statistics(hass, metadata, stat_data)
        imported_count += len(stat_data)
        _LOGGER.info("Statistiques ENGIE importées pour %s: %d point(s)", stat_id, len(stat_data))

    return imported_count
