"""Unit tests for DomoLink ENGIE Électricité & Gaz integration."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
import sys
import types
from typing import Any
from unittest.mock import MagicMock

try:
    import pytest
except ImportError:
    class _Approx:
        def __init__(self, val: float, rel: float = 1e-6, abs: float = 1e-12) -> None:
            self.val = val
            self.rel = rel
            self.abs = abs

        def __eq__(self, other: Any) -> bool:
            try:
                tol = max(self.abs, abs(self.val) * self.rel)
                return abs(float(other) - float(self.val)) <= tol
            except Exception:
                return False

    class _RaisesCtx:
        def __init__(self, exc_type: Any) -> None:
            self.exc_type = exc_type
            self.value: Any = None

        def __enter__(self) -> "_RaisesCtx":
            return self

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
            if exc_type is not None and issubclass(exc_type, self.exc_type):
                self.value = exc_val
                return True
            raise AssertionError(f"Expected {self.exc_type}, got {exc_type}")

    class _PytestStub:
        approx = _Approx
        raises = _RaisesCtx
        mark = types.SimpleNamespace(asyncio=lambda fn: fn)

    pytest = _PytestStub()  # type: ignore[assignment]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMP_DIR = os.path.join(REPO_ROOT, "custom_components", "domolink_engie")


def _install_ha_stubs() -> None:
    """Install lightweight Home Assistant stubs for standalone pytest execution."""
    if "aiohttp" not in sys.modules:
        try:
            import aiohttp  # noqa: F401
        except ImportError:
            aiohttp_stub = types.ModuleType("aiohttp")
            aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
            aiohttp_stub.ClientSession = MagicMock
            aiohttp_stub.ClientTimeout = MagicMock
            aiohttp_stub.CookieJar = MagicMock
            aiohttp_stub.TCPConnector = MagicMock
            sys.modules["aiohttp"] = aiohttp_stub

    if "voluptuous" not in sys.modules:
        try:
            import voluptuous  # noqa: F401
        except ImportError:
            vol_stub = types.ModuleType("voluptuous")
            vol_stub.Schema = lambda s: s
            vol_stub.Required = lambda k, **kw: k
            vol_stub.Optional = lambda k, **kw: k
            vol_stub.In = lambda v: v
            vol_stub.All = lambda *a: a
            vol_stub.Coerce = lambda t: t
            vol_stub.Range = lambda **kw: kw
            sys.modules["voluptuous"] = vol_stub

    if "homeassistant" in sys.modules:
        return

    ha = types.ModuleType("homeassistant")
    sys.modules["homeassistant"] = ha

    const_mod = types.ModuleType("homeassistant.const")
    const_mod.PERCENTAGE = "%"

    class UnitOfEnergy:
        KILO_WATT_HOUR = "kWh"

    class UnitOfVolume:
        CUBIC_METERS = "m³"

    class UnitOfApparentPower:
        VOLT_AMPERE = "VA"

    class UnitOfTemperature:
        CELSIUS = "°C"

    const_mod.UnitOfEnergy = UnitOfEnergy
    const_mod.UnitOfVolume = UnitOfVolume
    const_mod.UnitOfApparentPower = UnitOfApparentPower
    const_mod.UnitOfTemperature = UnitOfTemperature
    sys.modules["homeassistant.const"] = const_mod

    core_mod = types.ModuleType("homeassistant.core")
    core_mod.HomeAssistant = object
    core_mod.ServiceCall = object
    sys.modules["homeassistant.core"] = core_mod

    ce_mod = types.ModuleType("homeassistant.config_entries")
    ce_mod.ConfigEntry = object

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs: Any) -> None:
            pass

    class OptionsFlow:
        pass

    ce_mod.ConfigFlow = ConfigFlow
    ce_mod.OptionsFlow = OptionsFlow
    sys.modules["homeassistant.config_entries"] = ce_mod

    exc_mod = types.ModuleType("homeassistant.exceptions")
    exc_mod.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    sys.modules["homeassistant.exceptions"] = exc_mod

    data_entry_mod = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_mod.FlowResult = dict
    sys.modules["homeassistant.data_entry_flow"] = data_entry_mod

    helpers_mod = types.ModuleType("homeassistant.helpers")
    sys.modules["homeassistant.helpers"] = helpers_mod

    aio_mod = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aio_mod.async_get_clientsession = MagicMock()
    sys.modules["homeassistant.helpers.aiohttp_client"] = aio_mod

    ent_mod = types.ModuleType("homeassistant.helpers.entity")
    ent_mod.DeviceInfo = dict

    class EntityCategory:
        DIAGNOSTIC = "diagnostic"
        CONFIG = "config"

    ent_mod.EntityCategory = EntityCategory
    sys.modules["homeassistant.helpers.entity"] = ent_mod

    ep_mod = types.ModuleType("homeassistant.helpers.entity_platform")
    ep_mod.AddEntitiesCallback = Any
    sys.modules["homeassistant.helpers.entity_platform"] = ep_mod

    uc_mod = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __class_getitem__(cls, item: Any) -> Any:
            return cls

    class CoordinatorEntity:
        def __init__(self, coordinator: Any) -> None:
            self.coordinator = coordinator

        def __class_getitem__(cls, item: Any) -> Any:
            return cls

    class UpdateFailed(Exception):
        pass

    uc_mod.DataUpdateCoordinator = DataUpdateCoordinator
    uc_mod.CoordinatorEntity = CoordinatorEntity
    uc_mod.UpdateFailed = UpdateFailed
    sys.modules["homeassistant.helpers.update_coordinator"] = uc_mod

    comp_mod = types.ModuleType("homeassistant.components")
    sys.modules["homeassistant.components"] = comp_mod

    sens_mod = types.ModuleType("homeassistant.components.sensor")

    class SensorDeviceClass:
        ENERGY = "energy"
        GAS = "gas"
        MONETARY = "monetary"
        DATE = "date"
        APPARENT_POWER = "apparent_power"
        TEMPERATURE = "temperature"

    class SensorStateClass:
        MEASUREMENT = "measurement"
        TOTAL = "total"
        TOTAL_INCREASING = "total_increasing"

    from dataclasses import dataclass

    @dataclass(frozen=True, kw_only=True)
    class SensorEntityDescription:
        key: str
        name: str | None = None
        device_class: Any = None
        state_class: Any = None
        native_unit_of_measurement: Any = None
        suggested_display_precision: int | None = None
        entity_category: Any = None
        icon: str | None = None

    class SensorEntity:
        pass

    sens_mod.SensorDeviceClass = SensorDeviceClass
    sens_mod.SensorStateClass = SensorStateClass
    sens_mod.SensorEntityDescription = SensorEntityDescription
    sens_mod.SensorEntity = SensorEntity
    sys.modules["homeassistant.components.sensor"] = sens_mod

    bin_mod = types.ModuleType("homeassistant.components.binary_sensor")

    class BinarySensorDeviceClass:
        CONNECTIVITY = "connectivity"
        RUNNING = "running"

    @dataclass(frozen=True, kw_only=True)
    class BinarySensorEntityDescription:
        key: str
        name: str | None = None
        device_class: Any = None
        entity_category: Any = None
        icon: str | None = None

    class BinarySensorEntity:
        pass

    bin_mod.BinarySensorDeviceClass = BinarySensorDeviceClass
    bin_mod.BinarySensorEntityDescription = BinarySensorEntityDescription
    bin_mod.BinarySensorEntity = BinarySensorEntity
    sys.modules["homeassistant.components.binary_sensor"] = bin_mod

    diag_mod = types.ModuleType("homeassistant.components.diagnostics")
    diag_mod.async_redact_data = lambda d, keys: {
        k: ("**REDACTED**" if k in keys else v) for k, v in d.items()
    }
    sys.modules["homeassistant.components.diagnostics"] = diag_mod


_install_ha_stubs()

# Register package so relative imports work seamlessly
pkg = types.ModuleType("domolink_engie")
pkg.__path__ = [COMP_DIR]
sys.modules["domolink_engie"] = pkg


def _load_mod(name: str):
    spec = importlib.util.spec_from_file_location(
        f"domolink_engie.{name}",
        os.path.join(COMP_DIR, f"{name}.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"domolink_engie.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load_mod("const")
engie_api = _load_mod("engie_api")
zlinky = _load_mod("zlinky")
statistics = _load_mod("statistics")
coordinator = _load_mod("coordinator")
sensor = _load_mod("sensor")
binary_sensor = _load_mod("binary_sensor")
diagnostics = _load_mod("diagnostics")


def test_manifest_and_translations_valid():
    """Verify manifest, hacs.json, and all localization JSON files."""
    with open(os.path.join(COMP_DIR, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["domain"] == "domolink_engie"
    assert manifest["config_flow"] is True
    assert "recorder" in manifest["dependencies"]
    assert manifest["iot_class"] == "cloud_polling"

    for path in (
        os.path.join(COMP_DIR, "strings.json"),
        os.path.join(COMP_DIR, "translations", "fr.json"),
        os.path.join(COMP_DIR, "translations", "en.json"),
        os.path.join(REPO_ROOT, "hacs.json"),
    ):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)


def test_pkce_code_challenge_format():
    """Verify S256 PKCE verifier and challenge match RFC 7636."""
    verifier, challenge = engie_api.generate_pkce_pair()
    assert len(verifier) >= 43
    assert "=" not in challenge
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert challenge == expected


def test_auth_headers_dvmobile_and_erl():
    """Verify AuthInterceptor header replication for DVMobile, Cookie init, and ERL gateways."""
    client = engie_api.EngieApiClient(MagicMock(), access_token="tok_123")
    client.set_account_context(ref_bp="100200300", compte_contrat="600123456", id_personne="998877")

    dv_headers = client._default_headers(is_erl=False)
    assert dv_headers["Authorization"] == "Bearer tok_123"
    assert dv_headers["demandeur"] == "MOBILE_ANDROID"
    assert "DIG_ID_CLIENT" not in dv_headers
    assert "access_token=tok_123" in dv_headers["Cookie"]
    assert "Cel_Cc=600123456" in dv_headers["Cookie"]
    assert "Cel_Bp=100200300" in dv_headers["Cookie"]
    assert "idPersonne=998877" in dv_headers["Cookie"]

    cookie_headers = client._default_headers(is_cookie_init=True)
    assert cookie_headers["demandeur"] == "PARTICULIERS_ANDROID"

    erl_headers = client._default_headers(is_erl=True)
    assert erl_headers["demandeur"] == "ECARE4ERL-ANDROID"


def test_determine_user_type():
    """Verify ENGIE ConsoRepositoryImpl userType codes for mono/dual contracts."""
    assert engie_api.determine_user_type(True, True, True, True) == "DUALCO"
    assert engie_api.determine_user_type(True, False, True, False) == "ELECCO"
    assert engie_api.determine_user_type(False, True, False, True) == "GAZCO"
    assert engie_api.determine_user_type(True, True, True, False) == "DUALELECCO"
    assert engie_api.determine_user_type(True, True, False, True) == "DUALGAZCO"
    assert engie_api.determine_user_type(True, True, False, False) == "DUALNC"


def test_safe_float_french_formatting():
    """Verify safe_float handles French comma decimals and spaces."""
    assert engie_api.safe_float("1 234,56") == 1234.56
    assert engie_api.safe_float("0.2514") == 0.2514
    assert engie_api.safe_float("") is None
    assert engie_api.safe_float(None) is None


def test_extract_contract_summary():
    """Verify extraction of BusinessPartner, CompteContrat, Elec and Gaz contracts (live schema)."""
    sample = {
        "infosUsers": [
            {
                "refBP": "100200300",
                "idPersonne": None,
                "nom": "DUPONT",
                "prenom": "Jean",
                "listeCC": [
                    {
                        "numeroCC": "600123456",
                        "clientERL": False,
                        "typeTarif": "OM_AND_TR",
                        "adresse": {"nVoie": "12", "libelleVoie": "RUE DE LA PAIX", "cp": "75001", "ville": "PARIS"},
                        "listeContrat": [
                            {"id": "C1", "type": "ELEC", "typeEnergie": "Electricité", "puissance": "6"},
                            {"id": "C2", "type": "GAZ", "typeEnergie": "Gaz Naturel"},
                        ],
                    }
                ],
            }
        ]
    }
    summary = coordinator.extract_contract_summary(sample)
    assert summary["ref_bp"] == "100200300"
    assert summary["compte_contrat"] == "600123456"
    assert summary["code_postal"] == "75001"
    assert summary["has_elec"] is True
    assert summary["has_gaz"] is True
    assert summary["elec_contract"]["puissance"] == "6"


def test_parse_consumption_variation_and_jwt_id():
    """Verify live getConsumptionVariation normalization and JWT idPersonne extraction."""
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "sub": "user@example.com",
                "listeReferenceSI": [
                    {"application": "SYMPHONIE_DGP", "identifiantSi": "100200300"},
                    {"application": "XDATA_DGP", "identifiantSi": "998877"},
                ],
            }
        ).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    assert engie_api.extract_id_personne_from_jwt(f"hdr.{payload}.sig") == "998877"

    raw_var = {
        "comparaison": [
            {
                "type": "Mois",
                "date": "Octobre 2026",
                "consos": [
                    {"energie": "5E", "variation": "20%", "conso": 479.24, "prix": 96.96, "date": "2026-10-01", "parRapportA": "par rapport au mois précédent"}
                ],
            }
        ],
        "recap": {
            "date": "Octobre 2026",
            "consommations": [{"energie": "5E", "variation": 4, "conso": 5678.31, "consoN": 5465.75, "parRapportA": "par rapport à Oct. 2025"}],
        },
        "historiqueConso": {"sixMoisHistoElec": True, "sixMoisHistoGaz": False},
    }
    parsed = coordinator.parse_consumption_variation(raw_var)
    assert parsed["month_kwh"] == 479.24
    assert parsed["month_euro"] == 96.96
    assert parsed["month_variation_pct"] == 20.0
    assert parsed["year_kwh"] == 5678.31
    assert parsed["year_prev_kwh"] == 5465.75
    assert parsed["year_variation_pct"] == 4.0


def test_parse_engie_datetime_and_cumulative_series():
    """Verify top-of-hour UTC alignment and monotonic cumulative sums for recorder."""
    dt1 = statistics.parse_engie_datetime_utc("10/11/2026 14:35:12")
    assert dt1 == datetime(2026, 11, 10, 14, 0, 0, tzinfo=timezone.utc)

    dt2 = statistics.parse_engie_datetime_utc("2026-11-11")
    assert dt2 == datetime(2026, 11, 11, 0, 0, 0, tzinfo=timezone.utc)

    records = [
        {"valeurDate": "2026-11-11", "conso": 12.5},
        {"valeurDate": "2026-11-10", "conso": 10.0},
        {"valeurDate": "2026-11-10", "conso": 2.0},  # duplicate date merges cleanly
        {"valeurDate": "2026-11-09", "conso": -1.0},  # negative ignored
    ]
    series = statistics.build_cumulative_series(records, "conso")
    assert len(series) == 2
    assert series[0]["state"] == 12.0
    assert series[0]["sum"] == 12.0
    assert series[1]["state"] == 12.5
    assert series[1]["sum"] == 24.5


def test_sensor_metadata_energy_dashboard_compliance():
    """Verify Energy Dashboard metadata rules and value extraction on all ENGIE sensors."""
    mock_data = {
        "summary": {"has_elec": True, "has_gaz": True, "client_erl": True, "compte_contrat": "600123", "ref_bp": "100", "prenom": "J", "nom": "L"},
        "elec_releve": {"dernierIndex": "15230", "dernierIndexHP": "10000", "dernierIndexHC": "5230", "idPDL": "123456", "dateProchaineReleve": "26/11/2026"},
        "gaz_releve": {"dernierIndex": "4321", "idPDL": "GI123", "dateProchaineReleve": "26/11/2026"},
        "elec_daily": [
            {"valeurDate": "2026-11-09", "conso": 10.0, "consoEuro": 2.5},
            {"valeurDate": "2026-11-10", "conso": 14.2, "consoEuro": 3.55},
        ],
        "gaz_daily": [
            {"valeurDate": "2026-11-10", "volume": 3.5, "conso": 38.5, "consoEuro": 4.2},
        ],
        "elec_variation": {"month_kwh": 479.24, "month_euro": 96.96, "month_variation_pct": 20.0, "year_kwh": 5678.31},
        "gaz_variation": {"month_kwh": 1799.0, "month_euro": 282.96, "month_variation_pct": 358.0, "year_kwh": 26781.0},
        "est_elec": {"cadrans": [{"prixKwh": "0,2516"}]},
        "est_gaz": {"prixKwh": "0.112", "coefficient": "11.15"},
        "carbone": {"histoCarboneElec": [{"carboneConsomme": 450.0}]},
        "billing": {
            "solde": {"solde": -42.5, "dateProchaineFacture": "29/03/2027"},
            "plan_mens": {
                "listPlanMensCourant": [
                    {
                        "date": "12/03/2027",
                        "montantMensualite": 478.61,
                        "etatPaiement": "A Régler",
                        "detailMensualite": {"elec": 139.51, "gaz": 339.10},
                    }
                ]
            },
            "factures": {"listeFactures": [{"montantTTC": -765.06, "numeroFacture": "F123456789", "statutPaiement": "Réglé"}]},
            "paiements": {"listePaiements": [{"montantTTC": 478.61, "datePaiement": "09/11/2026", "moyenPaiement": "Prélèvement"}]},
            "engagement": {"solde": 10.0},
        },
        "erl": {"max_power": {"maxElecDay": 4500}},
    }

    by_key = {d.key: d for d in sensor.SENSOR_DESCRIPTIONS}

    # Verify strict HA rule: MONETARY cannot use MEASUREMENT state_class
    for desc in sensor.SENSOR_DESCRIPTIONS:
        if desc.device_class == "monetary":
            assert desc.state_class != "measurement", f"{desc.key} violates HA monetary rule"

    # Energy Dashboard electricity & gas checks
    assert by_key["elec_index_total"].device_class == "energy"
    assert by_key["elec_index_total"].state_class == "total_increasing"
    assert by_key["elec_index_total"].value_fn(mock_data) == 15230.0
    assert by_key["elec_monthly_conso"].value_fn(mock_data) == 24.2
    assert by_key["elec_annual_conso"].value_fn(mock_data) == 5678.31
    assert by_key["elec_kwh_price"].value_fn(mock_data) == 0.2516
    assert by_key["elec_variation"].value_fn(mock_data) == 20.0

    # Fallback check when elec_daily / gaz_daily are empty (maConso 403 fallback)
    fallback_data = {**mock_data, "elec_daily": [], "gaz_daily": []}
    assert by_key["elec_monthly_conso"].value_fn(fallback_data) == 479.24
    assert by_key["elec_monthly_cost"].value_fn(fallback_data) == 96.96
    assert by_key["gaz_monthly_kwh"].value_fn(fallback_data) == 1799.0
    assert by_key["gaz_monthly_cost"].value_fn(fallback_data) == 282.96

    assert by_key["gaz_index_m3"].device_class == "gas"
    assert by_key["gaz_index_m3"].native_unit_of_measurement == "m³"
    assert by_key["gaz_index_m3"].value_fn(mock_data) == 4321.0
    assert by_key["gaz_daily_m3"].value_fn(mock_data) == 3.5
    assert by_key["gaz_daily_kwh"].value_fn(mock_data) == 38.5
    assert by_key["gaz_annual_kwh"].value_fn(mock_data) == 26781.0
    assert by_key["gaz_conversion_coeff"].value_fn(mock_data) == 11.15

    assert by_key["account_solde"].value_fn(mock_data) == -42.5
    assert by_key["account_mensualite"].value_fn(mock_data) == 478.61
    assert by_key["account_last_invoice"].value_fn(mock_data) == -765.06
    assert by_key["account_last_payment"].value_fn(mock_data) == 478.61
    assert by_key["account_cagnotte_engagement"].value_fn(mock_data) == 10.0
    assert by_key["account_echeances_restantes"].value_fn(mock_data) == 1
    assert by_key["erl_max_power_day"].value_fn(mock_data) == 4500.0


def test_diagnostics_redacts_tokens_and_pii():
    """Verify diagnostics redacts sensitive credentials and PII."""
    assert "access_token" in diagnostics.TO_REDACT
    assert "refresh_token" in diagnostics.TO_REDACT
    assert "idPDL" in diagnostics.TO_REDACT
    assert "email" in diagnostics.TO_REDACT


def test_api_login_mfa_and_pkce_exchange_mocked():
    """Verify full async Okta MFA -> PKCE code -> token flow with mocked aiohttp."""

    class FakeResp:
        def __init__(self, status: int, json_data: dict[str, Any] | None = None, headers: dict[str, str] | None = None):
            self.status = status
            self._json = json_data or {}
            self.headers = headers or {}

        async def json(self, content_type=None):
            return self._json

        async def text(self):
            return json.dumps(self._json)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def post(self, url, **kwargs):
            if "api/v1/authn" in url and "verify" not in url:
                return FakeResp(
                    200,
                    {
                        "status": "MFA_REQUIRED",
                        "stateToken": "st_1",
                        "_embedded": {
                            "factors": [{"id": "f_sms", "factorType": "sms", "profile": {"phoneNumber": "+33600"}}],
                        },
                    },
                )
            if "verify" in url:
                body = kwargs.get("json") or {}
                if "passCode" in body:
                    return FakeResp(200, {"status": "SUCCESS", "sessionToken": "sess_xyz"})
                return FakeResp(200, {"status": "MFA_CHALLENGE", "stateToken": "st_2"})
            if "oauth/v1/token" in url:
                return FakeResp(
                    200,
                    {
                        "access_token": "acc_token_ok",
                        "refresh_token": "ref_token_ok",
                        "id_token": "id_ok",
                        "expires_in": 3600,
                    },
                )
            raise AssertionError(f"Unexpected POST {url}")

        def get(self, url, **kwargs):
            if "oauth/v1/authorize" in url:
                return FakeResp(302, headers={"Location": "engie:/loginCallback?code=auth_code_123&state=s"})
            raise AssertionError(f"Unexpected GET {url}")

    async def _run():
        client = engie_api.EngieApiClient(FakeSession())
        with pytest.raises(engie_api.EngieMfaRequired) as exc_info:
            await client.async_login_start("user@example.com", "secret")

        mfa = exc_info.value
        st2 = await client.async_send_mfa_challenge(mfa.state_token, mfa.factors[0])
        assert st2 == "st_2"

        tokens = await client.async_verify_mfa_code(st2, mfa.factors[0], "123456")
        assert tokens["access_token"] == "acc_token_ok"
        assert tokens["refresh_token"] == "ref_token_ok"

    asyncio.run(_run())


def test_config_flow_imports_and_factor_labels():
    """Verify config_flow loads cleanly and formats MFA factor labels."""
    cfg_flow = _load_mod("config_flow")
    assert cfg_flow._format_factor_label({"factorType": "sms", "profile": {"phoneNumber": "+33600000012"}}) == "SMS (+33600000012)"
    assert cfg_flow._format_factor_label({"factorType": "email", "profile": {"email": "a@b.fr"}}) == "E-mail (a@b.fr)"
    flow = cfg_flow.EngieConfigFlow()
    assert flow.VERSION == 1


def test_sec_ko_expiration_retry_and_endpoint_403_handling():
    """Verify SEC_KO_EXPIRATION renews session cookies and maConso 403 does not trigger EngieAuthError."""

    class FakeResp:
        def __init__(self, status: int, data: Any = None, set_cookies: list[str] | None = None):
            self.status = status
            self._data = data if data is not None else {}
            self._set_cookies = set_cookies or []
            self.headers = self

        def getall(self, name: str, default=None):
            if name.lower() == "set-cookie":
                return self._set_cookies
            return default or []

        async def json(self, content_type=None):
            return self._data

        async def text(self):
            return json.dumps(self._data)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def __init__(self):
            self.solde_calls = 0

        def post(self, url, **kwargs):
            return FakeResp(200, {"status": {"code": "OK"}}, set_cookies=["ClientIDCookie=DVMOBILE+abc; Path=/"])

        def request(self, method, url, **kwargs):
            if "getInfoUsers" in url:
                return FakeResp(200, {"infosUsers": []}, set_cookies=["secureAPIToken=new_tok; path=/"])
            if "soldeApayerV2" in url:
                self.solde_calls += 1
                if self.solde_calls == 1:
                    return FakeResp(403, {"code": "SEC_KO_EXPIRATION", "message": None})
                return FakeResp(200, {"solde": 0.0})
            if "estimationConsoElecMois" in url:
                return FakeResp(403, {"status": "Unauthorized"})
            raise AssertionError(f"Unexpected {url}")

    async def _run():
        client = engie_api.EngieApiClient(
            FakeSession(), access_token="acc_valid", refresh_token="ref_valid", token_expires_at=9999999999.0
        )
        solde = await client.async_get_solde("100200300", "600123456")
        assert solde == {"solde": 0.0}
        assert client._cookies.get("secureAPIToken") == "new_tok"

        with pytest.raises(engie_api.EngieEndpointUnauthorized):
            await client.async_get_estimation_elec("100200300", "600123456")

    asyncio.run(_run())


def test_v11_frontend_assets_options_flow_and_repartition_sensors():
    """Verify v1.1.0 frontend files, OptionsFlow, usage repartition sensors, and snapshot builder."""
    panel_js = os.path.join(COMP_DIR, "frontend", "domolink-engie-panel.js")
    card_js = os.path.join(COMP_DIR, "frontend", "domolink-engie-card.js")
    assert os.path.isfile(panel_js)
    assert os.path.isfile(card_js)

    cfg_flow = _load_mod("config_flow")
    fake_entry = MagicMock()
    fake_entry.options = {"scan_interval": 90, "billing_interval": 4}
    opt_flow = cfg_flow.EngieConfigFlow.async_get_options_flow(fake_entry)
    assert isinstance(opt_flow, cfg_flow.EngieOptionsFlowHandler)

    by_key = {d.key: d for d in sensor.SENSOR_DESCRIPTIONS}
    mock_data = {
        "billing": {
            "repartition_usages": {
                "repartitionUsagesElec": [
                    {"code": "FR", "libelle": "Froid", "valeur": 23.0, "couleur": "2EC8F4"},
                    {"code": "EC", "libelle": "Eclairage", "valeur": 13.0, "couleur": "29D46E"},
                ],
                "repartitionUsagesGaz": [
                    {"code": "CH", "libelle": "Chauffage", "valeur": 82.0, "couleur": "FFB700"},
                ],
            },
            "services_options": {
                "services": [
                    {"code": "ERL", "libelle": "Ma conso+", "prix": "2.99€TTC/mois", "souscriptionService": False},
                    {"code": "MENS", "libelle": "Mensualisation", "prix": None, "souscriptionService": True},
                ]
            },
            "cheque_energie": {"chequeEnergie": None, "encaissementCheque": False, "datePaiement": None},
        }
    }
    assert by_key["elec_usage_repartition"].value_fn(mock_data) == "Froid (23%)"
    assert by_key["gaz_usage_repartition"].value_fn(mock_data) == "Chauffage (82%)"
    assert by_key["account_services_options"].value_fn(mock_data) == 1
    assert by_key["account_cheque_energie"].value_fn(mock_data) == "Aucun"

    init_mod = _load_mod("__init__")
    fake_coord = MagicMock()
    fake_coord.compte_contrat = "600123456"
    fake_coord.data = {
        "summary": {"compte_contrat": "600123456", "has_elec": True, "has_gaz": True},
        "billing": mock_data["billing"],
    }
    snap = init_mod._build_account_snapshot(MagicMock(), "entry_1", fake_coord)
    assert snap["compte_contrat"] == "600123456"
    assert snap["has_elec"] is True
    assert snap["billing"]["repartition_usages"]["repartitionUsagesGaz"][0]["valeur"] == 82.0


def test_zlinky_tic_autodetection_and_manual_overrides():
    """Verify ZLinky_TIC auto-detection across Z2M/ZHA/deCONZ and manual sensor overrides."""
    fake_hass = MagicMock()
    fake_hass.states = {
        "sensor.zlinky_tic_sinsts": {"state": "840", "attributes": {"unit_of_measurement": "VA", "friendly_name": "ZLinky Puissance"}},
        "sensor.zlinky_tic_irms1": {"state": "3.6", "attributes": {"unit_of_measurement": "A", "friendly_name": "ZLinky Intensité"}},
        "sensor.zlinky_tic_urms1": {"state": "233", "attributes": {"unit_of_measurement": "V", "friendly_name": "ZLinky Tension"}},
        "sensor.zlinky_tic_east": {"state": "15242500", "attributes": {"unit_of_measurement": "Wh", "friendly_name": "ZLinky Index Total"}},
        "sensor.zlinky_tic_easf01": {"state": "5235.4", "attributes": {"unit_of_measurement": "kWh", "friendly_name": "ZLinky Index HC"}},
        "sensor.zlinky_tic_easf02": {"state": "10007.1", "attributes": {"unit_of_measurement": "kWh", "friendly_name": "ZLinky Index HP"}},
        "sensor.zlinky_tic_ptec": {"state": "HC..", "attributes": {"friendly_name": "ZLinky Tarif"}},
        "sensor.zlinky_tic_smaxsn": {"state": "4120", "attributes": {"unit_of_measurement": "VA"}},
        "sensor.zlinky_tic_isousc": {"state": "30", "attributes": {"unit_of_measurement": "A"}},
        "sensor.custom_power_override": {"state": "1250", "attributes": {"unit_of_measurement": "VA"}},
    }

    elec_releve = {"dernierIndex": "15230.0", "dernierIndexHC": "5230.0", "dernierIndexHP": "10000.0"}
    res = zlinky.detect_and_resolve_zlinky(fake_hass, options={}, elec_releve=elec_releve)

    assert res["detected"] is True
    assert res["device_prefix"] == "zlinky_tic"
    assert len(res["detected_devices"]) >= 1
    assert res["live"]["power_va"] == 840.0
    assert res["live"]["current_a"] == 3.6
    assert res["live"]["voltage_v"] == 233.0
    assert res["live"]["index_total_kwh"] == 15242.5  # Wh converted to kWh
    assert res["live"]["index_hc_kwh"] == 5235.4
    assert res["live"]["index_hp_kwh"] == 10007.1
    assert res["live"]["ptec"] == "HC.."
    assert res["live"]["is_hc"] is True
    assert res["live"]["max_power_va"] == 4120.0
    assert res["live"]["pref_kva"] == 6.0  # 30A -> 6 kVA
    assert res["live"]["daily_hc_kwh"] == 5.4
    assert res["live"]["daily_hp_kwh"] == 7.1
    assert res["live"]["daily_energy_kwh"] == 12.5

    # Verify manual override takes precedence over auto-detected entity
    res_manual = zlinky.detect_and_resolve_zlinky(
        fake_hass,
        options={"zlinky_power_sensor": "sensor.custom_power_override"},
        elec_releve=elec_releve,
    )
    assert res_manual["resolved"]["zlinky_power_sensor"] == "sensor.custom_power_override"
    assert res_manual["live"]["power_va"] == 1250.0

    # Verify value-aware & unit-aware matching: 1010 VA sensor beats 0 VA register and 19 W 'atlantic'/'diagnostic' sensor
    fake_hass_compete = MagicMock()
    fake_hass_compete.states = {
        "sensor.zlinky_tic_sinsts1": {"state": "0", "attributes": {"unit_of_measurement": "VA"}},
        "sensor.atlantic_chauffe_eau_active_power": {"state": "19", "attributes": {"unit_of_measurement": "W"}},
        "sensor.diagnostic_system_power": {"state": "19", "attributes": {"unit_of_measurement": "VA"}},
        "sensor.compteur_linky_puissance_apparente": {"state": "1010", "attributes": {"unit_of_measurement": "VA"}},
    }
    res_compete = zlinky.detect_and_resolve_zlinky(fake_hass_compete, options={})
    assert res_compete["resolved"]["zlinky_power_sensor"] == "sensor.compteur_linky_puissance_apparente"
    assert res_compete["live"]["power_va"] == 1010.0


def test_v13_new_sensors_hphc_regularisation_erl_and_initial_sum():
    """Verify new HP/HC sensors, regularization estimation, ERL avg power, and cumulative initial_sum."""
    records = [
        {"valeurDate": "2026-11-10", "conso": 10.0},
        {"valeurDate": "2026-11-11", "conso": 15.0},
    ]
    series = statistics.build_cumulative_series(records, "conso", initial_sum=100.0)
    assert series[0]["sum"] == 110.0
    assert series[1]["sum"] == 125.0

    by_key = {d.key: d for d in sensor.SENSOR_DESCRIPTIONS}
    mock_data = {
        "summary": {"has_elec": True, "has_gaz": True, "client_erl": True},
        "elec_daily": [{"valeurDate": "2026-11-10", "conso": 14.0, "consoEuro": 3.5, "temperature": 11.4}],
        "elec_monthly": [{"valeurDate": "2026-11-01", "conso": 310.0, "consoEuro": 75.0}],
        "gaz_monthly": [{"valeurDate": "2026-11-01", "conso": 820.0, "consoEuro": 95.0}],
        "est_elec": {
            "cadrans": [
                {"code": "HP", "prixKwh": "0,2700"},
                {"code": "HC", "prixKwh": "0,2068"},
            ]
        },
        "hphc_jour": {
            "historiquesElec": [
                {"valeurDate": "2026-11-10", "consoHP": 8.0, "consoHC": 6.0},
            ]
        },
        "billing": {
            "plan_mens": {
                "listPlanMensCourant": [
                    {"date": "12/09/2026", "montantMensualite": 200.0, "etatPaiement": "Réglé"},
                    {"date": "12/10/2026", "montantMensualite": 200.0, "etatPaiement": "Réglé"},
                    {"date": "12/11/2026", "montantMensualite": 200.0, "etatPaiement": "A Régler"},
                ]
            }
        },
        "erl": {"avg_power": {"avgPower": 680}, "max_power": {"maxElecDay": 4500}},
    }

    assert by_key["elec_price_hp"].value_fn(mock_data) == 0.27
    assert by_key["elec_price_hc"].value_fn(mock_data) == 0.2068
    assert by_key["elec_daily_hp"].value_fn(mock_data) == 8.0
    assert by_key["elec_daily_hc"].value_fn(mock_data) == 6.0
    assert by_key["elec_hc_ratio"].value_fn(mock_data) == 42.9
    assert by_key["ext_temperature"].value_fn(mock_data) == 11.4
    assert by_key["erl_avg_power"].value_fn(mock_data) == 680.0
    assert by_key["account_regularisation_estimee"].value_fn(mock_data) is not None


