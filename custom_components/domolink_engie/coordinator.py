"""DataUpdateCoordinator for DomoLink ENGIE Électricité & Gaz."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BILLING_REFRESH_SECONDS,
    CONF_ACCESS_TOKEN,
    CONF_BILLING_INTERVAL,
    CONF_CODE_POSTAL,
    CONF_COMPTE_CONTRAT,
    CONF_ID_PERSONNE,
    CONF_ID_TOKEN,
    CONF_IMPORT_HISTORY,
    CONF_REFRESH_TOKEN,
    CONF_REF_BP,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN_EXPIRES_AT,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    ERL_REFRESH_SECONDS,
    PAS_TEMPS_JOUR,
)
from .engie_api import (
    EngieApiClient,
    EngieApiError,
    EngieAuthError,
    determine_user_type,
    extract_id_personne_from_jwt,
    safe_float,
)
from .statistics import async_import_engie_statistics, parse_engie_datetime_utc

_LOGGER = logging.getLogger(__name__)


def _format_short_day_label(raw_dt: str) -> str:
    """Convert YYYY-MM-DD or DD/MM/YYYY into DD/MM for chart labels."""
    s = str(raw_dt or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}/{s[5:7]}"
    if len(s) >= 5 and s[2] == "/":
        return s[:5]
    return s[:5]


def _enrich_period_records(records: list[dict[str, Any]], is_daily: bool = True) -> list[dict[str, Any]]:
    """Enrich ENGIE consumption records with normalized chart keys while preserving raw fields."""
    enriched: list[dict[str, Any]] = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        raw_dt = str(rec.get("valeurDate") or rec.get("dateDebut") or rec.get("date") or "")
        kwh = safe_float(rec.get("conso") if rec.get("conso") is not None else rec.get("kwh"))
        prev = safe_float(rec.get("consoN1") or rec.get("consoAnneePrecedente") or rec.get("prev"))
        item = dict(rec)
        if raw_dt and "date" not in item:
            item["date"] = raw_dt
        if is_daily and raw_dt and "label" not in item:
            item["label"] = _format_short_day_label(raw_dt)
        if kwh is not None:
            item.setdefault("kwh", kwh)
            item.setdefault("curr", kwh)
        if prev is not None:
            item.setdefault("prev", prev)
        enriched.append(item)
    return enriched


def _enrich_hourly_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize hourly load curve records with hour (0..23), kwh, and euro fields."""
    enriched: list[dict[str, Any]] = []
    for idx, rec in enumerate(records or []):
        if not isinstance(rec, dict):
            continue
        raw_dt = str(rec.get("valeurDate") or rec.get("dateDebut") or rec.get("date") or "")
        dt_utc = parse_engie_datetime_utc(raw_dt) if raw_dt else None
        hour = dt_utc.hour if dt_utc is not None else (rec.get("hour") if isinstance(rec.get("hour"), int) else idx % 24)
        kwh = safe_float(rec.get("conso") if rec.get("conso") is not None else rec.get("kwh"))
        euro = safe_float(rec.get("consoEuro") if rec.get("consoEuro") is not None else rec.get("euro"))
        item = dict(rec)
        item.setdefault("hour", hour)
        if kwh is not None:
            item.setdefault("kwh", kwh)
            item.setdefault("value", kwh)
        if euro is not None:
            item.setdefault("euro", euro)
        enriched.append(item)
    return enriched


def _parse_pct(val: Any) -> float | None:
    """Parse percentage string like '20%' or '-12%' or numeric value."""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    return safe_float(str(val).replace("%", "").strip())


def parse_consumption_variation(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize getConsumptionVariation payload into monthly & rolling-annual metrics."""
    if not isinstance(raw, dict):
        return {}

    month_item: dict[str, Any] = {}
    month_label = ""
    for comp in raw.get("comparaison") or []:
        if not isinstance(comp, dict):
            continue
        if str(comp.get("type") or "").lower() == "mois":
            month_label = str(comp.get("date") or "")
            consos = comp.get("consos") or []
            if consos and isinstance(consos[0], dict):
                month_item = consos[0]
            break

    recap = raw.get("recap") or {}
    recap_list = recap.get("consommations") or []
    recap_item = recap_list[0] if recap_list and isinstance(recap_list[0], dict) else {}

    return {
        "month_label": month_label or str(recap.get("date") or ""),
        "month_date": month_item.get("date"),
        "month_kwh": safe_float(month_item.get("conso")),
        "month_euro": safe_float(month_item.get("prix")),
        "month_variation_pct": _parse_pct(month_item.get("variation")),
        "month_compare_to": month_item.get("parRapportA"),
        "year_kwh": safe_float(recap_item.get("conso")),
        "year_prev_kwh": safe_float(recap_item.get("consoN")),
        "year_variation_pct": _parse_pct(recap_item.get("variation")),
        "year_compare_to": recap_item.get("parRapportA"),
        "historique_conso": raw.get("historiqueConso") or {},
    }


def extract_contract_summary(
    info_users: dict[str, Any], target_cc: str | None = None
) -> dict[str, Any]:
    """Extract normalized BusinessPartner, CompteContrat, and Energy Contracts."""
    list_bp = info_users.get("infosUsers") or info_users.get("listBp") or []
    for bp in list_bp:
        ref_bp = str(bp.get("refBP") or "")
        id_personne = str(bp.get("idPersonne") or "")
        for cc in bp.get("listeCC") or []:
            num_cc = str(cc.get("numeroCC") or "")
            if target_cc and num_cc != target_cc:
                continue

            addr = cc.get("adresse") or {}
            cp = str(addr.get("cp") or "")
            ville = str(addr.get("ville") or "")
            contrats = cc.get("listeContrat") or []

            elec_contract = next(
                (
                    c
                    for c in contrats
                    if str(c.get("type") or c.get("typeEnergie") or "").upper().startswith("ELEC")
                ),
                None,
            )
            gaz_contract = next(
                (
                    c
                    for c in contrats
                    if str(c.get("type") or c.get("typeEnergie") or "").upper().startswith("GAZ")
                ),
                None,
            )

            return {
                "ref_bp": ref_bp,
                "id_personne": id_personne,
                "nom": bp.get("nom") or "",
                "prenom": bp.get("prenom") or "",
                "email": bp.get("email") or "",
                "segment": bp.get("segmentClientLibelle") or "",
                "compte_contrat": num_cc,
                "code_postal": cp,
                "ville": ville,
                "adresse_complete": f"{addr.get('nVoie', '')} {addr.get('libelleVoie', '')}, {cp} {ville}".strip(" ,"),
                "type_logement": cc.get("typeLogement") or "",
                "type_tarif": cc.get("typeTarif") or "",
                "etat_cc": cc.get("libelleEtat") or cc.get("etat") or "",
                "client_erl": bool(cc.get("clientERL") or cc.get("contratERLActif")),
                "has_elec": elec_contract is not None,
                "has_gaz": gaz_contract is not None,
                "elec_contract": elec_contract or {},
                "gaz_contract": gaz_contract or {},
                "offre_pilotage_elec": bool(bp.get("offreMonPilotageElec")),
                "offre_my_power": bool(bp.get("offreMyPower")),
            }
    return {}


class EngieDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator managing ENGIE API polling, token persistence, and history backfill."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: EngieApiClient,
    ) -> None:
        self.entry = entry
        self.client = client
        self.ref_bp: str = entry.data.get(CONF_REF_BP, "")
        self.compte_contrat: str = entry.data.get(CONF_COMPTE_CONTRAT, "")
        self.code_postal: str = entry.data.get(CONF_CODE_POSTAL, "")
        self.id_personne: str = entry.data.get(CONF_ID_PERSONNE, "")

        self._last_billing_fetch: float = 0.0
        self._billing_cache: dict[str, Any] = {}
        self._history_imported: bool = False

        options = getattr(entry, "options", None) or {}
        scan_mins = int(options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS // 60))
        self._billing_refresh_seconds = int(
            options.get(CONF_BILLING_INTERVAL, BILLING_REFRESH_SECONDS // 3600)
        ) * 3600

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.compte_contrat}",
            update_interval=timedelta(minutes=max(5, scan_mins)),
        )

    def apply_options(self) -> None:
        """Apply updated scan_interval and billing_interval options in-place."""
        options = getattr(self.entry, "options", None) or {}
        scan_mins = int(options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS // 60))
        self._billing_refresh_seconds = int(
            options.get(CONF_BILLING_INTERVAL, BILLING_REFRESH_SECONDS // 3600)
        ) * 3600
        self.update_interval = timedelta(minutes=max(5, scan_mins))

    async def _async_safe_call(self, label: str, coro: Any) -> dict[str, Any]:
        """Execute an optional ENGIE endpoint call, propagating auth errors and logging API restrictions."""
        try:
            res = await coro
            return res if isinstance(res, dict) else {}
        except EngieAuthError:
            raise
        except EngieApiError as err:
            _LOGGER.debug("%s notice: %s", label, err)
            return {}

    async def async_persist_tokens(self, tokens: dict[str, Any]) -> None:
        """Persist refreshed OAuth tokens into ConfigEntry storage."""
        new_data = dict(self.entry.data)
        new_data[CONF_ACCESS_TOKEN] = tokens.get("access_token")
        if tokens.get("refresh_token"):
            new_data[CONF_REFRESH_TOKEN] = tokens["refresh_token"]
        if tokens.get("id_token"):
            new_data[CONF_ID_TOKEN] = tokens["id_token"]
        new_data[CONF_TOKEN_EXPIRES_AT] = tokens.get("token_expires_at", 0.0)
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest ENGIE account, meter, consumption, tariff, and billing data."""
        try:
            await self.client.async_init_session_cookies(
                self.ref_bp, self.compte_contrat, self.id_personne
            )
            info_users = await self.client.async_get_info_users()
            summary = extract_contract_summary(info_users, target_cc=self.compte_contrat or None)
            if not summary:
                raise UpdateFailed("Aucun contrat ENGIE actif trouvé pour ce compte.")

            self.ref_bp = summary["ref_bp"] or self.ref_bp
            self.compte_contrat = summary["compte_contrat"] or self.compte_contrat
            self.code_postal = summary["code_postal"] or self.code_postal
            self.id_personne = (
                summary["id_personne"]
                or self.id_personne
                or extract_id_personne_from_jwt(self.client.access_token)
            )
            self.client.set_account_context(self.ref_bp, self.compte_contrat, self.id_personne)

            has_elec = summary["has_elec"]
            has_gaz = summary["has_gaz"]

            # 1. Fetch Meter Readings & Indexes
            releve = await self.client.async_get_releve(self.ref_bp, self.compte_contrat)
            elec_rel = releve.get("contratElec") or {}
            gaz_rel = releve.get("contratGaz") or {}
            elec_comm = bool(elec_rel.get("compteurCommuniquant", True))
            gaz_comm = bool(gaz_rel.get("compteurCommuniquant", True))

            user_type = determine_user_type(has_elec, has_gaz, elec_comm, gaz_comm)

            now_dt = datetime.now(timezone.utc)
            end_ms = int(now_dt.timestamp() * 1000)
            start_45d_ms = int((now_dt - timedelta(days=45)).timestamp() * 1000)
            start_7d_ms = int((now_dt - timedelta(days=7)).timestamp() * 1000)
            start_2d_ms = int((now_dt - timedelta(days=2)).timestamp() * 1000)
            start_year_ms = int(now_dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)

            # 2. Fetch Consumption, Variations, Tariffs & Hourly curves concurrently
            conso_tasks: dict[str, Any] = {
                "ma_conso_jour": self._async_safe_call(
                    "courbe/maConso",
                    self.client.async_get_ma_conso(
                        self.ref_bp,
                        self.compte_contrat,
                        self.code_postal,
                        user_type,
                        start_45d_ms,
                        end_ms,
                        pas_temps=PAS_TEMPS_JOUR,
                    ),
                ),
            }

            if has_elec:
                conso_tasks["elec_variation_raw"] = self._async_safe_call(
                    "getConsumptionVariation ELEC",
                    self.client.async_get_consumption_variation(
                        self.ref_bp, self.compte_contrat, self.code_postal, "ELEC", elec_comm, gaz_comm
                    ),
                )
                conso_tasks["histo_elec_mois"] = self._async_safe_call(
                    "histoElecMois",
                    self.client.async_get_histo_elec_mois(
                        self.ref_bp, self.compte_contrat, self.code_postal, start_year_ms, end_ms, elec_comm
                    ),
                )
                conso_tasks["est_elec"] = self._async_safe_call(
                    "estimationElec",
                    self.client.async_get_estimation_elec(self.ref_bp, self.compte_contrat),
                )
                conso_tasks["hphc_jour"] = self._async_safe_call(
                    "repartitionHeuresCreusesJour",
                    self.client.async_get_repartition_hphc_jour(
                        self.ref_bp, self.compte_contrat, start_7d_ms, end_ms
                    ),
                )
                conso_tasks["histo_elec_heures"] = self._async_safe_call(
                    "histoElecHeures",
                    self.client.async_get_histo_elec_heures(
                        self.ref_bp, self.compte_contrat, start_2d_ms, end_ms
                    ),
                )
                pdl = str(elec_rel.get("idPDL") or "")
                if pdl and self.id_personne:
                    conso_tasks["carbone"] = self._async_safe_call(
                        "histoCarboneElec",
                        self.client.async_get_histo_carbone(
                            self.ref_bp,
                            self.compte_contrat,
                            self.id_personne,
                            pdl,
                            now_dt.replace(day=1).strftime("%Y-%m-%d"),
                        ),
                    )

            if has_gaz:
                conso_tasks["gaz_variation_raw"] = self._async_safe_call(
                    "getConsumptionVariation GAZ",
                    self.client.async_get_consumption_variation(
                        self.ref_bp, self.compte_contrat, self.code_postal, "GAZ", elec_comm, gaz_comm
                    ),
                )
                conso_tasks["histo_gaz_mois"] = self._async_safe_call(
                    "histoGazMois",
                    self.client.async_get_histo_gaz_mois(
                        self.ref_bp, self.compte_contrat, self.code_postal, start_year_ms, end_ms, gaz_comm
                    ),
                )
                conso_tasks["est_gaz"] = self._async_safe_call(
                    "estimationGaz",
                    self.client.async_get_estimation_gaz(self.ref_bp, self.compte_contrat),
                )

            task_keys = list(conso_tasks.keys())
            task_values = await asyncio.gather(*(conso_tasks[k] for k in task_keys))
            conso_res = dict(zip(task_keys, task_values))

            ma_conso_jour = conso_res.get("ma_conso_jour") or {}
            histo_elec_mois = conso_res.get("histo_elec_mois") or {}
            histo_gaz_mois = conso_res.get("histo_gaz_mois") or {}
            histo_elec_heures = conso_res.get("histo_elec_heures") or {}
            est_elec = conso_res.get("est_elec") or {}
            est_gaz = conso_res.get("est_gaz") or {}
            hphc_jour = conso_res.get("hphc_jour") or {}
            carbone = conso_res.get("carbone") or {}
            elec_variation_raw = conso_res.get("elec_variation_raw") or {}
            gaz_variation_raw = conso_res.get("gaz_variation_raw") or {}

            elec_variation = parse_consumption_variation(elec_variation_raw)
            gaz_variation = parse_consumption_variation(gaz_variation_raw)

            # 3. Billing, Invoices, Payments, Engagement & Consents (throttled every 6h, parallelized)
            now_ts = time.time()
            if not self._billing_cache or (now_ts - self._last_billing_fetch) >= self._billing_refresh_seconds:
                t_en = "DUAL" if (has_elec and has_gaz) else ("ELEC" if has_elec else "GAZ")
                billing_tasks = {
                    "solde": self._async_safe_call(
                        "soldeApayerV2", self.client.async_get_solde(self.ref_bp, self.compte_contrat)
                    ),
                    "plan_mens": self._async_safe_call(
                        "planMensV2", self.client.async_get_plan_mens(self.ref_bp, self.compte_contrat)
                    ),
                    "factures": self._async_safe_call(
                        "listeFacture", self.client.async_get_factures(self.ref_bp, self.compte_contrat)
                    ),
                    "paiements": self._async_safe_call(
                        "paiements", self.client.async_get_paiements(self.ref_bp, self.compte_contrat)
                    ),
                    "engagement": self._async_safe_call(
                        "engagementSolde", self.client.async_get_engagement_solde(self.ref_bp, self.compte_contrat)
                    ),
                    "cheque_energie": self._async_safe_call(
                        "chequeEnergie", self.client.async_get_info_cheque_energie(self.ref_bp, self.compte_contrat)
                    ),
                    "services_options": self._async_safe_call(
                        "servicesEtOptions",
                        self.client.async_get_services_options(self.ref_bp, self.compte_contrat, type_energie=t_en),
                    ),
                    "repartition_usages": self._async_safe_call(
                        "repartitionUsages",
                        self.client.async_get_repartition_usages_courbe(
                            self.ref_bp, self.compte_contrat, user_type, t_en, start_45d_ms, end_ms
                        ),
                    ),
                    "accords": self._async_safe_call(
                        "accords", self.client.async_get_accords(self.ref_bp, self.compte_contrat)
                    ),
                }
                b_keys = list(billing_tasks.keys())
                b_vals = await asyncio.gather(*(billing_tasks[k] for k in b_keys))
                self._billing_cache = dict(zip(b_keys, b_vals))
                self._last_billing_fetch = now_ts

            # 4. ERL Live Telemetry (if active) + fast polling adjustment
            erl_data: dict[str, Any] = {}
            if summary.get("client_erl"):
                options = getattr(self.entry, "options", None) or {}
                if CONF_SCAN_INTERVAL not in options:
                    self.update_interval = timedelta(seconds=ERL_REFRESH_SECONDS)
                erl_data = await self._async_safe_call(
                    "ERL live", self.client.async_get_erl_live(self.compte_contrat)
                )

            # 5. Compute Normalized Totals for Sensors & Charts
            elec_hist = _enrich_period_records(ma_conso_jour.get("historiquesElec") or [], is_daily=True)
            gaz_hist = _enrich_period_records(ma_conso_jour.get("historiquesGaz") or [], is_daily=True)

            elec_months = _enrich_period_records(histo_elec_mois.get("historiquesElec") or [], is_daily=False)
            gaz_months = _enrich_period_records(histo_gaz_mois.get("historiquesGaz") or [], is_daily=False)
            elec_hourly = _enrich_hourly_records((histo_elec_heures.get("historiquesElec") or [])[-24:])

            # Trigger retroactive history import once after initial setup if enabled
            if not self._history_imported and self.entry.data.get(CONF_IMPORT_HISTORY, True):
                self._history_imported = True
                self.hass.async_create_task(self.async_import_full_history(months=12))

            return {
                "summary": summary,
                "user_type": user_type,
                "releve": releve,
                "elec_releve": elec_rel,
                "gaz_releve": gaz_rel,
                "elec_daily": elec_hist,
                "gaz_daily": gaz_hist,
                "elec_monthly": elec_months,
                "gaz_monthly": gaz_months,
                "elec_hourly": elec_hourly,
                "elec_monthly_raw": histo_elec_mois,
                "gaz_monthly_raw": histo_gaz_mois,
                "elec_variation": elec_variation,
                "gaz_variation": gaz_variation,
                "est_elec": est_elec,
                "est_gaz": est_gaz,
                "hphc_jour": hphc_jour,
                "carbone": carbone,
                "billing": self._billing_cache,
                "erl": erl_data,
                "fiabilite_data": ma_conso_jour.get("fiabiliteData") or "",
                "last_sync_utc": now_dt.isoformat(),
            }

        except EngieAuthError as err:
            raise ConfigEntryAuthFailed(f"Authentification ENGIE expirée: {err}") from err
        except EngieApiError as err:
            raise UpdateFailed(f"Erreur API ENGIE: {err}") from err

    async def async_import_full_history(self, months: int = 12) -> int:
        """Backfill up to N months of historical energy & gas consumption into HA Recorder."""
        if not self.data:
            return 0
        summary = self.data.get("summary") or {}
        user_type = self.data.get("user_type") or "DUALCO"

        now_dt = datetime.now(timezone.utc)
        end_ms = int(now_dt.timestamp() * 1000)
        start_hist_ms = int((now_dt - timedelta(days=30 * months)).timestamp() * 1000)

        elec_records: list[dict[str, Any]] = []
        gaz_records: list[dict[str, Any]] = []

        try:
            try:
                conso_hist = await self.client.async_get_ma_conso(
                    self.ref_bp,
                    self.compte_contrat,
                    self.code_postal,
                    user_type,
                    start_hist_ms,
                    end_ms,
                    pas_temps=PAS_TEMPS_JOUR,
                )
                elec_records = list(conso_hist.get("historiquesElec") or [])
                gaz_records = list(conso_hist.get("historiquesGaz") or [])
            except EngieApiError as err:
                _LOGGER.debug("courbe/maConso backfill notice: %s", err)

            # Merge recent 30d hourly points for Electricity without double-counting covered days
            if summary.get("has_elec"):
                try:
                    start_30d_ms = int((now_dt - timedelta(days=30)).timestamp() * 1000)
                    hourly = await self.client.async_get_histo_elec_heures(
                        self.ref_bp, self.compte_contrat, start_30d_ms, end_ms
                    )
                    hourly_elec = hourly.get("historiquesElec") or []
                    if hourly_elec:
                        hourly_dates = {
                            dt.date()
                            for rec in hourly_elec
                            if isinstance(rec, dict)
                            for dt in [
                                parse_engie_datetime_utc(
                                    rec.get("valeurDate") or rec.get("dateDebut") or rec.get("date")
                                )
                            ]
                            if dt is not None
                        }
                        if hourly_dates and elec_records:
                            elec_records = [
                                rec
                                for rec in elec_records
                                if (
                                    (
                                        dt := parse_engie_datetime_utc(
                                            rec.get("valeurDate") or rec.get("dateDebut") or rec.get("date")
                                        )
                                    )
                                    is None
                                    or dt.date() not in hourly_dates
                                )
                            ]
                        elec_records = elec_records + list(hourly_elec)
                except EngieApiError as err:
                    _LOGGER.debug("histoElecHeures backfill notice: %s", err)

            # Fallback to monthly history or monthly variation datapoints if maConso returned empty/403
            if not elec_records:
                if self.data.get("elec_monthly"):
                    elec_records = list(self.data["elec_monthly"])
                else:
                    ev = self.data.get("elec_variation") or {}
                    if ev.get("month_date") and ev.get("month_kwh") is not None:
                        elec_records = [{"valeurDate": ev["month_date"], "conso": ev["month_kwh"]}]
            if not gaz_records:
                if self.data.get("gaz_monthly"):
                    gaz_records = list(self.data["gaz_monthly"])
                else:
                    gv = self.data.get("gaz_variation") or {}
                    if gv.get("month_date") and gv.get("month_kwh") is not None:
                        gaz_records = [{"valeurDate": gv["month_date"], "conso": gv["month_kwh"]}]

            if not elec_records and not gaz_records:
                return 0

            return await async_import_engie_statistics(
                self.hass,
                self.compte_contrat,
                elec_records,
                gaz_records,
            )
        except Exception as err:
            _LOGGER.warning("Échec partiel import historique ENGIE: %s", err)
            return 0
