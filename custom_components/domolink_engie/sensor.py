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


def _extract_usage_repartition(data: dict[str, Any], list_key: str) -> tuple[str | None, dict[str, Any]]:
    """Extract top usage category and full percentage map from courbe/getRepartitionUsages."""
    rep = (data.get("billing") or {}).get("repartition_usages") or {}
    items = rep.get(list_key) or []
    if not isinstance(items, list) or not items:
        return None, {}
    valid = [it for it in items if isinstance(it, dict) and it.get("libelle")]
    if not valid:
        return None, {}
    top = max(valid, key=lambda it: safe_float(it.get("valeur")) or 0.0)
    breakdown = {
        str(it["libelle"]): safe_float(it.get("valeur"))
        for it in valid
        if safe_float(it.get("valeur")) is not None
    }
    top_val = safe_float(top.get("valeur"))
    label = f"{top['libelle']} ({top_val:.0f}%)" if top_val is not None else str(top["libelle"])
    return label, {"repartition": breakdown, "items": valid}


def _extract_services_options(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Extract active subscribed services count & list from servicesEtOptions."""
    svc_raw = (data.get("billing") or {}).get("services_options") or {}
    services = svc_raw.get("services") or []
    if not isinstance(services, list):
        return 0, {}
    active = [s.get("libelle") or s.get("code") for s in services if isinstance(s, dict) and s.get("souscriptionService")]
    summary = [
        {
            "code": s.get("code"),
            "libelle": s.get("libelle"),
            "prix": s.get("prix"),
            "souscrit": bool(s.get("souscriptionService")),
        }
        for s in services
        if isinstance(s, dict)
    ]
    return len(active), {"services_actifs": active, "catalogue": summary}


def _extract_elec_monthly_kwh(data: dict[str, Any]) -> float | None:
    daily = _sum_current_month(data.get("elec_daily") or [], "conso")
    if daily is not None:
        return daily
    monthly_rec = _latest_record(data.get("elec_monthly") or [])
    monthly_val = safe_float(monthly_rec.get("conso"))
    if monthly_val is not None:
        return monthly_val
    return safe_float((data.get("elec_variation") or {}).get("month_kwh"))


def _extract_elec_monthly_cost(data: dict[str, Any]) -> float | None:
    daily = _sum_current_month(data.get("elec_daily") or [], "consoEuro")
    if daily is not None:
        return daily
    monthly_rec = _latest_record(data.get("elec_monthly") or [])
    monthly_val = safe_float(monthly_rec.get("consoEuro"))
    if monthly_val is not None:
        return monthly_val
    return safe_float((data.get("elec_variation") or {}).get("month_euro"))


def _extract_gaz_monthly_kwh(data: dict[str, Any]) -> float | None:
    daily = _sum_current_month(data.get("gaz_daily") or [], "conso")
    if daily is not None:
        return daily
    monthly_rec = _latest_record(data.get("gaz_monthly") or [])
    monthly_val = safe_float(monthly_rec.get("conso"))
    if monthly_val is not None:
        return monthly_val
    return safe_float((data.get("gaz_variation") or {}).get("month_kwh"))


def _extract_gaz_monthly_cost(data: dict[str, Any]) -> float | None:
    daily = _sum_current_month(data.get("gaz_daily") or [], "consoEuro")
    if daily is not None:
        return daily
    monthly_rec = _latest_record(data.get("gaz_monthly") or [])
    monthly_val = safe_float(monthly_rec.get("consoEuro"))
    if monthly_val is not None:
        return monthly_val
    return safe_float((data.get("gaz_variation") or {}).get("month_euro"))


def _extract_hphc_latest(data: dict[str, Any]) -> dict[str, Any]:
    """Extract latest daily HP/HC breakdown from getRepartitionHeuresCreusesJour or elec_daily."""
    hphc = data.get("hphc_jour") or {}
    candidates = (
        hphc.get("historiquesElec")
        or hphc.get("repartitionHeuresCreuses")
        or hphc.get("listeConsos")
        or data.get("elec_daily")
        or []
    )
    for rec in reversed(candidates if isinstance(candidates, list) else []):
        if not isinstance(rec, dict):
            continue
        hp = safe_float(
            rec.get("consoHP")
            if rec.get("consoHP") is not None
            else (rec.get("heuresPleines") if rec.get("heuresPleines") is not None else rec.get("hp"))
        )
        hc = safe_float(
            rec.get("consoHC")
            if rec.get("consoHC") is not None
            else (rec.get("heuresCreuses") if rec.get("heuresCreuses") is not None else rec.get("hc"))
        )
        if hp is not None or hc is not None:
            hp_v = hp or 0.0
            hc_v = hc or 0.0
            tot = hp_v + hc_v
            ratio = round((hc_v / tot) * 100.0, 1) if tot > 0 else None
            return {
                "hp_kwh": round(hp_v, 2) if hp is not None else None,
                "hc_kwh": round(hc_v, 2) if hc is not None else None,
                "hc_ratio_pct": ratio,
                "date": rec.get("valeurDate") or rec.get("dateDebut") or rec.get("date"),
            }

    # Check top-level summary fields in hphc_jour if present
    hp_top = safe_float(hphc.get("consoHP") or hphc.get("heuresPleines"))
    hc_top = safe_float(hphc.get("consoHC") or hphc.get("heuresCreuses"))
    pct_top = safe_float(hphc.get("tauxHeuresCreuses") or hphc.get("pourcentageHC"))
    if hp_top is not None or hc_top is not None or pct_top is not None:
        tot = (hp_top or 0.0) + (hc_top or 0.0)
        ratio = pct_top if pct_top is not None else (round(((hc_top or 0.0) / tot) * 100.0, 1) if tot > 0 else None)
        return {
            "hp_kwh": round(hp_top, 2) if hp_top is not None else None,
            "hc_kwh": round(hc_top, 2) if hc_top is not None else None,
            "hc_ratio_pct": ratio,
            "date": hphc.get("date"),
        }
    return {}


def _extract_elec_cadran_price(data: dict[str, Any], target: str) -> float | None:
    """Extract HP or HC specific kWh tariff from est_elec.cadrans."""
    cadrans = (data.get("est_elec") or {}).get("cadrans") or []
    if not isinstance(cadrans, list) or not cadrans:
        return None
    target_up = target.upper()
    for cad in cadrans:
        if not isinstance(cad, dict):
            continue
        lbl = str(cad.get("code") or cad.get("libelle") or cad.get("typeCadran") or "").upper()
        px = safe_float(cad.get("prixKwh"))
        if px is None or px <= 0:
            continue
        if target_up == "HP" and ("HP" in lbl or "PLEINE" in lbl):
            return round(px, 5)
        if target_up == "HC" and ("HC" in lbl or "CREUSE" in lbl):
            return round(px, 5)
    return None


def _extract_ext_temperature(data: dict[str, Any]) -> float | None:
    """Extract latest external average temperature from daily or monthly records."""
    for key in ("elec_daily", "gaz_daily", "elec_monthly", "gaz_monthly"):
        for rec in reversed(data.get(key) or []):
            if isinstance(rec, dict):
                temp = safe_float(rec.get("temperature") or rec.get("temperatureExt"))
                if temp is not None:
                    return round(temp, 1)
    return None


def _extract_regularisation_estimee(data: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
    """Estimate annual regularization delta (paid installments minus estimated real consumption cost)."""
    pm = ((data.get("billing") or {}).get("plan_mens") or {}).get("listPlanMensCourant") or []
    if not isinstance(pm, list) or not pm:
        return None, {}

    paid_items = [
        it
        for it in pm
        if isinstance(it, dict)
        and not str(it.get("etatPaiement") or "").lower().startswith("a régler")
        and safe_float(it.get("montantMensualite")) is not None
    ]
    pending_items = [
        it
        for it in pm
        if isinstance(it, dict)
        and str(it.get("etatPaiement") or "").lower().startswith("a régler")
        and safe_float(it.get("montantMensualite")) is not None
    ]

    total_paid = round(sum(safe_float(it.get("montantMensualite")) or 0.0 for it in paid_items), 2)
    current_mens = safe_float(_extract_plan_mens_info(data)[0].get("montantMensualite")) or 0.0
    elec_cost_m = _extract_elec_monthly_cost(data) or 0.0
    gaz_cost_m = _extract_gaz_monthly_cost(data) or 0.0
    real_monthly_burn = round(elec_cost_m + gaz_cost_m, 2)

    if real_monthly_burn <= 0 or current_mens <= 0:
        return None, {}

    months_elapsed = max(1, len(paid_items))
    est_consumed = round(real_monthly_burn * months_elapsed, 2)
    base_paid = total_paid if total_paid > 0 else round(current_mens * months_elapsed, 2)
    ecart = round(base_paid - est_consumed, 2)

    if ecart > 5.0:
        statut = "Trop-perçu (remboursement estimé)"
    elif ecart < -5.0:
        statut = "Reste à payer estimé"
    else:
        statut = "Équilibré"

    return ecart, {
        "statut_regularisation": statut,
        "mensualites_cumulees_eur": base_paid,
        "conso_valorisee_cumulee_eur": est_consumed,
        "mensualite_actuelle_eur": current_mens,
        "cout_mensuel_reel_eur": real_monthly_burn,
        "echeances_reglees": len(paid_items),
        "echeances_restantes": len(pending_items),
    }


def _extract_erl_avg_power(data: dict[str, Any]) -> float | None:
    """Extract live average power (VA) from ERL gateway response."""
    avg_p = ((data.get("erl") or {}).get("avg_power") or {})
    if not isinstance(avg_p, dict):
        return None
    for k in ("avgPower", "power", "value", "apparentPower"):
        val = safe_float(avg_p.get(k))
        if val is not None:
            return val
    pts = avg_p.get("values") or avg_p.get("metrics") or avg_p.get("data") or []
    if isinstance(pts, list) and pts:
        last = pts[-1]
        if isinstance(last, dict):
            return safe_float(last.get("value") or last.get("power") or last.get("avgPower"))
        return safe_float(last)
    return None


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
        key="elec_price_hp",
        name="Tarif Électricité Heures Pleines",
        device_group="elec",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="EUR/kWh",
        suggested_display_precision=4,
        icon="mdi:tag-plus-outline",
        condition_fn=lambda d: _extract_elec_cadran_price(d, "HP") is not None,
        value_fn=lambda d: _extract_elec_cadran_price(d, "HP"),
    ),
    EngieSensorDescription(
        key="elec_price_hc",
        name="Tarif Électricité Heures Creuses",
        device_group="elec",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="EUR/kWh",
        suggested_display_precision=4,
        icon="mdi:tag-minus-outline",
        condition_fn=lambda d: _extract_elec_cadran_price(d, "HC") is not None,
        value_fn=lambda d: _extract_elec_cadran_price(d, "HC"),
    ),
    EngieSensorDescription(
        key="elec_daily_hp",
        name="Consommation Électricité Veille HP",
        device_group="elec",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:white-balance-sunny",
        condition_fn=lambda d: _extract_hphc_latest(d).get("hp_kwh") is not None,
        value_fn=lambda d: _extract_hphc_latest(d).get("hp_kwh"),
        attrs_fn=lambda d: {"date": _extract_hphc_latest(d).get("date")},
    ),
    EngieSensorDescription(
        key="elec_daily_hc",
        name="Consommation Électricité Veille HC",
        device_group="elec",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:weather-night",
        condition_fn=lambda d: _extract_hphc_latest(d).get("hc_kwh") is not None,
        value_fn=lambda d: _extract_hphc_latest(d).get("hc_kwh"),
        attrs_fn=lambda d: {"date": _extract_hphc_latest(d).get("date")},
    ),
    EngieSensorDescription(
        key="elec_hc_ratio",
        name="Ratio Heures Creuses Électricité",
        device_group="elec",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        icon="mdi:percent-circle-outline",
        condition_fn=lambda d: _extract_hphc_latest(d).get("hc_ratio_pct") is not None,
        value_fn=lambda d: _extract_hphc_latest(d).get("hc_ratio_pct"),
        attrs_fn=lambda d: {
            "hp_kwh": _extract_hphc_latest(d).get("hp_kwh"),
            "hc_kwh": _extract_hphc_latest(d).get("hc_kwh"),
            "date": _extract_hphc_latest(d).get("date"),
        },
    ),
    EngieSensorDescription(
        key="ext_temperature",
        name="Température Extérieure Moyenne",
        device_group="account",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="°C",
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:thermometer",
        condition_fn=lambda d: _extract_ext_temperature(d) is not None,
        value_fn=_extract_ext_temperature,
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
            "echeancier": [
                {
                    "date": it.get("date"),
                    "montant": safe_float(it.get("montantMensualite")),
                    "etat": it.get("etatPaiement"),
                }
                for it in (((d.get("billing") or {}).get("plan_mens") or {}).get("listPlanMensCourant") or [])[:6]
                if isinstance(it, dict)
            ],
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
    EngieSensorDescription(
        key="elec_usage_repartition",
        name="Répartition Usages Électricité",
        device_group="elec",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chart-donut",
        condition_fn=lambda d: bool(((d.get("billing") or {}).get("repartition_usages") or {}).get("repartitionUsagesElec")),
        value_fn=lambda d: _extract_usage_repartition(d, "repartitionUsagesElec")[0],
        attrs_fn=lambda d: _extract_usage_repartition(d, "repartitionUsagesElec")[1],
    ),
    EngieSensorDescription(
        key="gaz_usage_repartition",
        name="Répartition Usages Gaz",
        device_group="gaz",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chart-donut",
        condition_fn=lambda d: bool(((d.get("billing") or {}).get("repartition_usages") or {}).get("repartitionUsagesGaz")),
        value_fn=lambda d: _extract_usage_repartition(d, "repartitionUsagesGaz")[0],
        attrs_fn=lambda d: _extract_usage_repartition(d, "repartitionUsagesGaz")[1],
    ),
    EngieSensorDescription(
        key="account_services_options",
        name="Options & Services ENGIE Actifs",
        device_group="account",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:puzzle-check-outline",
        condition_fn=lambda d: bool(((d.get("billing") or {}).get("services_options") or {}).get("services")),
        value_fn=lambda d: _extract_services_options(d)[0],
        attrs_fn=lambda d: _extract_services_options(d)[1],
    ),
    EngieSensorDescription(
        key="account_cheque_energie",
        name="Chèque Énergie",
        device_group="account",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:ticket-percent-outline",
        condition_fn=lambda d: bool((d.get("billing") or {}).get("cheque_energie")),
        value_fn=lambda d: (
            "Encaissé"
            if ((d.get("billing") or {}).get("cheque_energie") or {}).get("encaissementCheque")
            else "Aucun"
        ),
        attrs_fn=lambda d: {
            "montant": safe_float(((d.get("billing") or {}).get("cheque_energie") or {}).get("chequeEnergie")),
            "date_paiement": ((d.get("billing") or {}).get("cheque_energie") or {}).get("datePaiement"),
        },
    ),
    EngieSensorDescription(
        key="account_regularisation_estimee",
        name="Estimation Régularisation Annuelle",
        device_group="account",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:scale-balance",
        condition_fn=lambda d: _extract_regularisation_estimee(d)[0] is not None,
        value_fn=lambda d: _extract_regularisation_estimee(d)[0],
        attrs_fn=lambda d: _extract_regularisation_estimee(d)[1],
    ),
    # --- ERL LIVE TELEMETRY (if active) ---
    EngieSensorDescription(
        key="erl_avg_power",
        name="Puissance Instantanée (ERL)",
        device_group="erl",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        icon="mdi:flash-alert",
        condition_fn=lambda d: bool((d.get("summary") or {}).get("client_erl"))
        and _extract_erl_avg_power(d) is not None,
        value_fn=_extract_erl_avg_power,
    ),
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
        self._attr_translation_key = description.key
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
