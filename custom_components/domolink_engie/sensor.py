"""Sensor platform for DomoLink ENGIE Électricité & Gaz (Energy Dashboard ready)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfApparentPower,
    UnitOfEnergy,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EngieDataUpdateCoordinator
from .engie_api import safe_float

_LOGGER = logging.getLogger(__name__)


def _parse_date(raw: Any) -> date | None:
    """Parse ENGIE ISO or dd/MM/yyyy date string into date."""
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _latest_record(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return latest non-null consumption record."""
    for rec in reversed(records or []):
        if isinstance(rec, dict) and (rec.get("conso") is not None or rec.get("volume") is not None):
            return rec
    return records[-1] if records else {}


def _sum_current_month(records: list[dict[str, Any]], key: str) -> float | None:
    """Sum daily records belonging to the latest recorded month."""
    if not records:
        return None
    latest = _latest_record(records)
    ref_date = _parse_date(latest.get("valeurDate") or latest.get("dateDebut") or latest.get("date"))
    if not ref_date:
        return None
    total = 0.0
    found = False
    for rec in records:
        dt = _parse_date(rec.get("valeurDate") or rec.get("dateDebut") or rec.get("date"))
        if dt and dt.year == ref_date.year and dt.month == ref_date.month:
            val = safe_float(rec.get(key))
            if val is not None:
                total += val
                found = True
    return round(total, 3) if found else None


def _extract_elec_kwh_price(data: dict[str, Any]) -> float | None:
    cadrans = (data.get("est_elec") or {}).get("cadrans") or []
    for cadran in cadrans:
        px = safe_float(cadran.get("prixKwh"))
        if px is not None and px > 0:
            return round(px, 5)
    latest = _latest_record(data.get("elec_daily") or [])
    conso = safe_float(latest.get("conso"))
    euro = safe_float(latest.get("consoEuro"))
    if conso and euro and conso > 0:
        return round(euro / conso, 5)
    return None


def _extract_gaz_kwh_price(data: dict[str, Any]) -> float | None:
    px = safe_float((data.get("est_gaz") or {}).get("prixKwh"))
    if px is not None and px > 0:
        return round(px, 5)
    latest = _latest_record(data.get("gaz_daily") or [])
    conso = safe_float(latest.get("conso"))
    euro = safe_float(latest.get("consoEuro"))
    if conso and euro and conso > 0:
        return round(euro / conso, 5)
    return None


def _extract_gaz_index_m3(data: dict[str, Any]) -> float | None:
    idx = safe_float((data.get("gaz_releve") or {}).get("dernierIndex"))
    if idx is not None:
        return idx
    return safe_float((data.get("est_gaz") or {}).get("indexConsoM3"))


def _extract_plan_mens_info(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Extract current monthly installment entry & count from planMensV2 or legacy schema."""
    pm = (data.get("billing") or {}).get("plan_mens") or {}
    items = pm.get("listPlanMensCourant") or []
    if isinstance(items, list) and items:
        pending = [it for it in items if isinstance(it, dict) and str(it.get("etatPaiement", "")).lower().startswith("a régler")]
        current = pending[-1] if pending else (items[0] if isinstance(items[0], dict) else {})
        return current, len(pending) if pending else len(items)
    return pm, int(pm.get("nbEcheancesRestantes") or 0) if pm.get("nbEcheancesRestantes") is not None else 0


def _extract_latest_invoice(data: dict[str, Any]) -> dict[str, Any]:
    """Extract latest invoice from listeFacture (live array) or derniereFacture fallback."""
    factures = (data.get("billing") or {}).get("factures") or {}
    if isinstance(factures.get("derniereFacture"), dict):
        return factures["derniereFacture"]
    liste = factures.get("listeFactures") or []
    if isinstance(liste, list) and liste and isinstance(liste[0], dict):
        return liste[0]
    return {}


def _extract_latest_payment(data: dict[str, Any]) -> dict[str, Any]:
    """Extract latest payment from recupererlistepaiements."""
    paiements = (data.get("billing") or {}).get("paiements") or {}
    liste = paiements.get("listePaiements") or []
    if isinstance(liste, list) and liste and isinstance(liste[0], dict):
        return liste[0]
    return {}


def _extract_elec_monthly_kwh(data: dict[str, Any]) -> float | None:
    daily = _sum_current_month(data.get("elec_daily") or [], "conso")
    if daily is not None:
        return daily
    return safe_float((data.get("elec_variation") or {}).get("month_kwh"))


def _extract_elec_monthly_cost(data: dict[str, Any]) -> float | None:
    daily = _sum_current_month(data.get("elec_daily") or [], "consoEuro")
    if daily is not None:
        return daily
    return safe_float((data.get("elec_variation") or {}).get("month_euro"))


def _extract_gaz_monthly_kwh(data: dict[str, Any]) -> float | None:
    daily = _sum_current_month(data.get("gaz_daily") or [], "conso")
    if daily is not None:
        return daily
    return safe_float((data.get("gaz_variation") or {}).get("month_kwh"))


def _extract_gaz_monthly_cost(data: dict[str, Any]) -> float | None:
    daily = _sum_current_month(data.get("gaz_daily") or [], "consoEuro")
    if daily is not None:
        return daily
    return safe_float((data.get("gaz_variation") or {}).get("month_euro"))


@dataclass(frozen=True, kw_only=True)
class EngieSensorDescription(SensorEntityDescription):
    """Describe an ENGIE sensor with dynamic extraction logic."""

    device_group: str  # "elec" | "gaz" | "account" | "erl"
    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    condition_fn: Callable[[dict[str, Any]], bool] | None = None


SENSOR_DESCRIPTIONS: tuple[EngieSensorDescription, ...] = (
    # --- ELECTRICITY (LINKY) ---
    EngieSensorDescription(
        key="elec_index_total",
        name="Index Électricité Total",
        device_group="elec",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:flash",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_elec"))
        and safe_float((d.get("elec_releve") or {}).get("dernierIndex")) is not None,
        value_fn=lambda d: safe_float((d.get("elec_releve") or {}).get("dernierIndex")),
        attrs_fn=lambda d: {
            "pdl": (d.get("elec_releve") or {}).get("idPDL"),
            "numero_serie": (d.get("elec_releve") or {}).get("numeroSerie"),
            "date_dernier_index": (d.get("elec_releve") or {}).get("dateDernierIndex"),
            "type_comptage": (d.get("elec_releve") or {}).get("typeComptage"),
            "puissance_souscrite": ((d.get("summary") or {}).get("elec_contract") or {}).get("puissance"),
        },
    ),
    EngieSensorDescription(
        key="elec_index_hp",
        name="Index Électricité Heures Pleines",
        device_group="elec",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:flash-outline",
        condition_fn=lambda d: safe_float((d.get("elec_releve") or {}).get("dernierIndexHP")) is not None,
        value_fn=lambda d: safe_float((d.get("elec_releve") or {}).get("dernierIndexHP")),
    ),
    EngieSensorDescription(
        key="elec_index_hc",
        name="Index Électricité Heures Creuses",
        device_group="elec",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:weather-night",
        condition_fn=lambda d: safe_float((d.get("elec_releve") or {}).get("dernierIndexHC")) is not None,
        value_fn=lambda d: safe_float((d.get("elec_releve") or {}).get("dernierIndexHC")),
    ),
    EngieSensorDescription(
        key="elec_daily_conso",
        name="Consommation Électricité Veille",
        device_group="elec",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:lightning-bolt",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_elec")) and bool(d.get("elec_daily")),
        value_fn=lambda d: safe_float(_latest_record(d.get("elec_daily") or []).get("conso")),
        attrs_fn=lambda d: {
            "date": _latest_record(d.get("elec_daily") or []).get("valeurDate"),
            "lissage": _latest_record(d.get("elec_daily") or []).get("lissage"),
            "temperature_ext": _latest_record(d.get("elec_daily") or []).get("temperature"),
            "fiabilite": d.get("fiabilite_data"),
        },
    ),
    EngieSensorDescription(
        key="elec_daily_cost",
        name="Coût Électricité Veille",
        device_group="elec",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:currency-eur",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_elec")) and bool(d.get("elec_daily")),
        value_fn=lambda d: safe_float(_latest_record(d.get("elec_daily") or []).get("consoEuro")),
    ),
    EngieSensorDescription(
        key="elec_monthly_conso",
        name="Consommation Électricité Mois",
        device_group="elec",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:calendar-month",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_elec")),
        value_fn=_extract_elec_monthly_kwh,
        attrs_fn=lambda d: {
            "periode": (d.get("elec_variation") or {}).get("month_label"),
            "date": (d.get("elec_variation") or {}).get("month_date"),
        },
    ),
    EngieSensorDescription(
        key="elec_monthly_cost",
        name="Coût Électricité Mois",
        device_group="elec",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:cash-multiple",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_elec")),
        value_fn=_extract_elec_monthly_cost,
        attrs_fn=lambda d: {
            "periode": (d.get("elec_variation") or {}).get("month_label"),
            "date": (d.get("elec_variation") or {}).get("month_date"),
        },
    ),
    EngieSensorDescription(
        key="elec_annual_conso",
        name="Consommation Électricité Annuelle",
        device_group="elec",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:calendar-range",
        condition_fn=lambda d: safe_float((d.get("elec_variation") or {}).get("year_kwh")) is not None,
        value_fn=lambda d: safe_float((d.get("elec_variation") or {}).get("year_kwh")),
        attrs_fn=lambda d: {
            "conso_annee_precedente": safe_float((d.get("elec_variation") or {}).get("year_prev_kwh")),
            "variation_pct": safe_float((d.get("elec_variation") or {}).get("year_variation_pct")),
            "comparaison": (d.get("elec_variation") or {}).get("year_compare_to"),
        },
    ),
    EngieSensorDescription(
        key="elec_kwh_price",
        name="Tarif kWh Électricité",
        device_group="elec",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="EUR/kWh",
        suggested_display_precision=4,
        icon="mdi:tag-outline",
        condition_fn=lambda d: _extract_elec_kwh_price(d) is not None,
        value_fn=_extract_elec_kwh_price,
    ),
    EngieSensorDescription(
        key="elec_carbon_month",
        name="Empreinte Carbone Électricité Mois",
        device_group="elec",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="gCO₂eq",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:molecule-co2",
        condition_fn=lambda d: bool(((d.get("carbone") or {}).get("histoCarboneElec") or [])),
        value_fn=lambda d: safe_float(
            ((d.get("carbone") or {}).get("histoCarboneElec") or [{}])[-1].get("carboneConsomme")
        ),
    ),
    EngieSensorDescription(
        key="elec_variation",
        name="Variation Consommation Électricité",
        device_group="elec",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:trending-up",
        condition_fn=lambda d: safe_float((d.get("elec_variation") or {}).get("month_variation_pct")) is not None,
        value_fn=lambda d: safe_float((d.get("elec_variation") or {}).get("month_variation_pct")),
        attrs_fn=lambda d: {"comparaison": (d.get("elec_variation") or {}).get("month_compare_to")},
    ),
    EngieSensorDescription(
        key="elec_next_reading_date",
        name="Prochain Relevé Électricité",
        device_group="elec",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:calendar-today",
        condition_fn=lambda d: _parse_date((d.get("elec_releve") or {}).get("dateProchaineReleve")) is not None,
        value_fn=lambda d: _parse_date((d.get("elec_releve") or {}).get("dateProchaineReleve")),
    ),
    # --- GAS (GAZPAR) ---
    EngieSensorDescription(
        key="gaz_index_m3",
        name="Index Compteur Gaz",
        device_group="gaz",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
        icon="mdi:fire",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_gaz")),
        value_fn=_extract_gaz_index_m3,
        attrs_fn=lambda d: {
            "pce": (d.get("gaz_releve") or {}).get("idPDL"),
            "numero_serie": (d.get("gaz_releve") or {}).get("numeroSerie"),
            "date_dernier_index": (d.get("gaz_releve") or {}).get("dateDernierIndex"),
            "coefficient_conversion": safe_float((d.get("est_gaz") or {}).get("coefficient")),
        },
    ),
    EngieSensorDescription(
        key="gaz_daily_m3",
        name="Consommation Gaz Veille (m³)",
        device_group="gaz",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
        icon="mdi:gas-burner",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_gaz")) and bool(d.get("gaz_daily")),
        value_fn=lambda d: safe_float(_latest_record(d.get("gaz_daily") or []).get("volume")),
        attrs_fn=lambda d: {
            "date": _latest_record(d.get("gaz_daily") or []).get("valeurDate"),
            "lissage": _latest_record(d.get("gaz_daily") or []).get("lissage"),
            "temperature_ext": _latest_record(d.get("gaz_daily") or []).get("temperature"),
        },
    ),
    EngieSensorDescription(
        key="gaz_daily_kwh",
        name="Consommation Gaz Veille (kWh)",
        device_group="gaz",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:fire-circle",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_gaz")) and bool(d.get("gaz_daily")),
        value_fn=lambda d: safe_float(_latest_record(d.get("gaz_daily") or []).get("conso")),
    ),
    EngieSensorDescription(
        key="gaz_daily_cost",
        name="Coût Gaz Veille",
        device_group="gaz",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:currency-eur",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_gaz")) and bool(d.get("gaz_daily")),
        value_fn=lambda d: safe_float(_latest_record(d.get("gaz_daily") or []).get("consoEuro")),
    ),
    EngieSensorDescription(
        key="gaz_monthly_m3",
        name="Consommation Gaz Mois (m³)",
        device_group="gaz",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
        icon="mdi:calendar-month",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_gaz")) and bool(d.get("gaz_daily")),
        value_fn=lambda d: _sum_current_month(d.get("gaz_daily") or [], "volume"),
    ),
    EngieSensorDescription(
        key="gaz_monthly_kwh",
        name="Consommation Gaz Mois (kWh)",
        device_group="gaz",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:calendar-month",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_gaz")),
        value_fn=_extract_gaz_monthly_kwh,
        attrs_fn=lambda d: {
            "periode": (d.get("gaz_variation") or {}).get("month_label"),
            "date": (d.get("gaz_variation") or {}).get("month_date"),
        },
    ),
    EngieSensorDescription(
        key="gaz_monthly_cost",
        name="Coût Gaz Mois",
        device_group="gaz",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:cash-multiple",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("has_gaz")),
        value_fn=_extract_gaz_monthly_cost,
        attrs_fn=lambda d: {
            "periode": (d.get("gaz_variation") or {}).get("month_label"),
            "date": (d.get("gaz_variation") or {}).get("month_date"),
        },
    ),
    EngieSensorDescription(
        key="gaz_annual_kwh",
        name="Consommation Gaz Annuelle (kWh)",
        device_group="gaz",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:calendar-range",
        condition_fn=lambda d: safe_float((d.get("gaz_variation") or {}).get("year_kwh")) is not None,
        value_fn=lambda d: safe_float((d.get("gaz_variation") or {}).get("year_kwh")),
        attrs_fn=lambda d: {
            "conso_annee_precedente": safe_float((d.get("gaz_variation") or {}).get("year_prev_kwh")),
            "variation_pct": safe_float((d.get("gaz_variation") or {}).get("year_variation_pct")),
            "comparaison": (d.get("gaz_variation") or {}).get("year_compare_to"),
        },
    ),
    EngieSensorDescription(
        key="gaz_kwh_price",
        name="Tarif kWh Gaz",
        device_group="gaz",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="EUR/kWh",
        suggested_display_precision=4,
        icon="mdi:tag-outline",
        condition_fn=lambda d: _extract_gaz_kwh_price(d) is not None,
        value_fn=_extract_gaz_kwh_price,
    ),
    EngieSensorDescription(
        key="gaz_variation",
        name="Variation Consommation Gaz",
        device_group="gaz",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:trending-up",
        condition_fn=lambda d: safe_float((d.get("gaz_variation") or {}).get("month_variation_pct")) is not None,
        value_fn=lambda d: safe_float((d.get("gaz_variation") or {}).get("month_variation_pct")),
        attrs_fn=lambda d: {"comparaison": (d.get("gaz_variation") or {}).get("month_compare_to")},
    ),
    EngieSensorDescription(
        key="gaz_next_reading_date",
        name="Prochain Relevé Gaz",
        device_group="gaz",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:calendar-today",
        condition_fn=lambda d: _parse_date((d.get("gaz_releve") or {}).get("dateProchaineReleve")) is not None,
        value_fn=lambda d: _parse_date((d.get("gaz_releve") or {}).get("dateProchaineReleve")),
    ),
    EngieSensorDescription(
        key="gaz_conversion_coeff",
        name="Coefficient Conversion Gaz",
        device_group="gaz",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="kWh/m³",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        icon="mdi:calculator-variant",
        condition_fn=lambda d: safe_float((d.get("est_gaz") or {}).get("coefficient")) is not None,
        value_fn=lambda d: safe_float((d.get("est_gaz") or {}).get("coefficient")),
    ),
    # --- ACCOUNT & BILLING ---
    EngieSensorDescription(
        key="account_solde",
        name="Solde à Payer",
        device_group="account",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:wallet-outline",
        value_fn=lambda d: safe_float(((d.get("billing") or {}).get("solde") or {}).get("solde")),
        attrs_fn=lambda d: {
            "titulaire": f"{(d.get('summary') or {}).get('prenom', '')} {(d.get('summary') or {}).get('nom', '')}".strip(),
            "adresse": (d.get("summary") or {}).get("adresse_complete"),
            "ref_bp": (d.get("summary") or {}).get("ref_bp"),
            "compte_contrat": (d.get("summary") or {}).get("compte_contrat"),
            "type_tarif": (d.get("summary") or {}).get("type_tarif"),
            "solde_cheque_energie": safe_float(((d.get("billing") or {}).get("solde") or {}).get("soldeCE")),
        },
    ),
    EngieSensorDescription(
        key="account_mensualite",
        name="Mensualité Globale",
        device_group="account",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:calendar-sync",
        value_fn=lambda d: safe_float(
            _extract_plan_mens_info(d)[0].get("montantMensualite")
            if _extract_plan_mens_info(d)[0].get("montantMensualite") is not None
            else _extract_plan_mens_info(d)[0].get("mensualite")
        ),
        attrs_fn=lambda d: {
            "mensualite_elec": safe_float(
                (_extract_plan_mens_info(d)[0].get("detailMensualite") or {}).get("elec")
                or _extract_plan_mens_info(d)[0].get("mensualiteElec")
            ),
            "mensualite_gaz": safe_float(
                (_extract_plan_mens_info(d)[0].get("detailMensualite") or {}).get("gaz")
                or _extract_plan_mens_info(d)[0].get("mensualiteGaz")
            ),
            "prochaine_echeance": _extract_plan_mens_info(d)[0].get("date"),
            "etat_paiement": _extract_plan_mens_info(d)[0].get("etatPaiement"),
        },
    ),
    EngieSensorDescription(
        key="account_next_invoice_date",
        name="Date Prochaine Facture",
        device_group="account",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-clock",
        value_fn=lambda d: _parse_date(
            ((d.get("billing") or {}).get("solde") or {}).get("dateProchaineFacture")
            or _extract_plan_mens_info(d)[0].get("date")
            or ((d.get("billing") or {}).get("plan_mens") or {}).get("dateFacture")
        ),
    ),
    EngieSensorDescription(
        key="account_last_invoice",
        name="Dernière Facture",
        device_group="account",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:receipt-text-outline",
        value_fn=lambda d: safe_float(_extract_latest_invoice(d).get("montantTTC")),
        attrs_fn=lambda d: {
            "numero": _extract_latest_invoice(d).get("numeroFacture"),
            "libelle": _extract_latest_invoice(d).get("libelle"),
            "date": _extract_latest_invoice(d).get("dateFacture"),
            "echeance": _extract_latest_invoice(d).get("dateEcheance"),
            "statut": _extract_latest_invoice(d).get("statutPaiement"),
        },
    ),
    EngieSensorDescription(
        key="account_last_payment",
        name="Dernier Prélèvement",
        device_group="account",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:bank-transfer-out",
        condition_fn=lambda d: bool(_extract_latest_payment(d)),
        value_fn=lambda d: safe_float(_extract_latest_payment(d).get("montantTTC")),
        attrs_fn=lambda d: {
            "date": _extract_latest_payment(d).get("datePaiement"),
            "moyen": _extract_latest_payment(d).get("moyenPaiement"),
            "statut": _extract_latest_payment(d).get("statutPaiement"),
        },
    ),
    EngieSensorDescription(
        key="account_cagnotte_engagement",
        name="Cagnotte Engagement ENGIE",
        device_group="account",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:piggy-bank-outline",
        condition_fn=lambda d: safe_float(((d.get("billing") or {}).get("engagement") or {}).get("solde")) is not None,
        value_fn=lambda d: safe_float(((d.get("billing") or {}).get("engagement") or {}).get("solde")),
    ),
    EngieSensorDescription(
        key="account_echeances_restantes",
        name="Échéances Mensuelles Restantes",
        device_group="account",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:counter",
        value_fn=lambda d: _extract_plan_mens_info(d)[1],
    ),
    # --- ERL LIVE TELEMETRY (if active) ---
    EngieSensorDescription(
        key="erl_max_power_day",
        name="Puissance Max Jour (ERL)",
        device_group="erl",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        icon="mdi:gauge",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("client_erl"))
        and safe_float(((d.get("erl") or {}).get("max_power") or {}).get("maxElecDay")) is not None,
        value_fn=lambda d: safe_float(((d.get("erl") or {}).get("max_power") or {}).get("maxElecDay")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ENGIE sensors based on active energy contracts."""
    coordinator: EngieDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}

    entities: list[EngieSensor] = []
    for desc in SENSOR_DESCRIPTIONS:
        if desc.condition_fn is None or desc.condition_fn(data):
            entities.append(EngieSensor(coordinator, desc))

    async_add_entities(entities)


class EngieSensor(CoordinatorEntity[EngieDataUpdateCoordinator], SensorEntity):
    """Representation of an ENGIE sensor entity."""

    entity_description: EngieSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EngieDataUpdateCoordinator,
        description: EngieSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        cc = coordinator.compte_contrat
        self._attr_unique_id = f"engie_{cc}_{description.key}"

        group_names = {
            "elec": f"ENGIE Électricité ({cc})",
            "gaz": f"ENGIE Gaz ({cc})",
            "erl": f"ENGIE ERL Linky ({cc})",
            "account": f"ENGIE Compte ({cc})",
        }
        group_models = {
            "elec": "Compteur Linky",
            "gaz": "Compteur Gazpar",
            "erl": "Passerelle ERL",
            "account": "Contrat Particuliers",
        }
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{cc}_{description.device_group}")},
            name=group_names.get(description.device_group, f"ENGIE ({cc})"),
            manufacturer="ENGIE",
            model=group_models.get(description.device_group, "ENGIE Particuliers"),
        )

    @property
    def native_value(self) -> Any:
        """Return computed sensor value."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return supplementary ENGIE metadata."""
        if not self.coordinator.data or not self.entity_description.attrs_fn:
            return None
        return {
            k: v
            for k, v in self.entity_description.attrs_fn(self.coordinator.data).items()
            if v is not None
        }
