"""Config Flow for DomoLink ENGIE Électricité & Gaz."""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult

from .const import (
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
    CONF_USERNAME,
    CONF_ZLINKY_DEVICE_PREFIX,
    CONF_ZLINKY_ENABLED,
    DOMAIN,
    ZLINKY_SENSOR_KEYS,
)
from .engie_api import (
    EngieApiClient,
    EngieApiError,
    EngieAuthError,
    EngieMfaRequired,
    async_create_engie_aiohttp_session,
    extract_id_personne_from_jwt,
)
from .zlinky import detect_and_resolve_zlinky

_LOGGER = logging.getLogger(__name__)


def _format_factor_label(factor: dict[str, Any]) -> str:
    """Build a human-readable label for an Okta MFA factor."""
    ftype = str(factor.get("factorType") or "").lower()
    profile = factor.get("profile") or {}
    if ftype == "sms":
        return f"SMS ({profile.get('phoneNumber', 'Téléphone')})"
    if ftype == "email":
        return f"E-mail ({profile.get('email', 'Adresse e-mail')})"
    if "totp" in ftype:
        return "Application d'authentification (OTP)"
    if ftype == "call":
        return f"Appel vocal ({profile.get('phoneNumber', 'Téléphone')})"
    return f"{factor.get('provider', 'Okta')} ({ftype})"


class EngieConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for DomoLink ENGIE Électricité & Gaz."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str = ""
        self._import_history: bool = True
        self._state_token: str = ""
        self._factors: list[dict[str, Any]] = []
        self._selected_factor: dict[str, Any] = {}
        self._tokens: dict[str, Any] = {}
        self._cookies: dict[str, str] = {}
        self._contracts_choices: dict[str, dict[str, Any]] = {}
        self._reauth_entry: config_entries.ConfigEntry | None = None

    @asynccontextmanager
    async def _async_client(self):
        """Provide a short-lived EngieApiClient whose session is always closed after the step."""
        session = await async_create_engie_aiohttp_session(getattr(self, "hass", None))
        client = EngieApiClient(
            session,
            access_token=self._tokens.get("access_token"),
            refresh_token=self._tokens.get("refresh_token"),
            id_token=self._tokens.get("id_token"),
            token_expires_at=float(self._tokens.get("token_expires_at", 0.0)),
        )
        client._cookies.update(self._cookies)
        try:
            yield client
        finally:
            self._cookies.update(client._cookies)
            if not session.closed:
                await session.close()

    async def _async_attempt_login(
        self, username: str, password: str, errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        """Attempt Okta login + initial MFA dispatch; returns a ConfigFlowResult on transition or None on error."""
        async with self._async_client() as client:
            try:
                self._tokens = await client.async_login_start(username, password)
            except EngieMfaRequired as mfa:
                self._state_token = mfa.state_token
                self._factors = [
                    f
                    for f in mfa.factors
                    if str(f.get("factorType", "")).lower() in ("sms", "email", "token:software:totp", "call")
                ] or mfa.factors

                if not self._factors:
                    errors["base"] = "no_mfa_factor"
                    return None
                if len(self._factors) == 1:
                    self._selected_factor = self._factors[0]
                    try:
                        if str(self._selected_factor.get("factorType", "")).lower() != "token:software:totp":
                            self._state_token = await client.async_send_mfa_challenge(
                                self._state_token, self._selected_factor
                            )
                        return await self.async_step_mfa_code()
                    except EngieApiError:
                        errors["base"] = "cannot_connect"
                        return None
                return await self.async_step_mfa_select()
            except EngieAuthError:
                errors["base"] = "invalid_auth"
                return None
            except EngieApiError:
                errors["base"] = "cannot_connect"
                return None
            except Exception as err:
                _LOGGER.exception("Unexpected ENGIE login error: %s", err)
                errors["base"] = "unknown"
                return None

        return await self._async_after_tokens()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Email and Password authentication."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            password = user_input["password"]
            self._import_history = bool(user_input.get(CONF_IMPORT_HISTORY, True))
            result = await self._async_attempt_login(self._username, password, errors)
            if result is not None:
                return result

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=self._username): str,
                vol.Required("password"): str,
                vol.Optional(CONF_IMPORT_HISTORY, default=True): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_mfa_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2a: Choose SMS or Email MFA factor when multiple are enrolled."""
        errors: dict[str, str] = {}
        factor_map = {str(f["id"]): f for f in self._factors if "id" in f}
        options = {fid: _format_factor_label(f) for fid, f in factor_map.items()}

        if user_input is not None:
            fid = user_input["factor_id"]
            self._selected_factor = factor_map[fid]
            async with self._async_client() as client:
                try:
                    if str(self._selected_factor.get("factorType", "")).lower() != "token:software:totp":
                        self._state_token = await client.async_send_mfa_challenge(
                            self._state_token, self._selected_factor
                        )
                    return await self.async_step_mfa_code()
                except EngieApiError:
                    errors["base"] = "cannot_connect"

        schema = vol.Schema({vol.Required("factor_id"): vol.In(options)})
        return self.async_show_form(step_id="mfa_select", data_schema=schema, errors=errors)

    async def async_step_mfa_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2b: Verify the one-time passcode received by SMS/Email."""
        errors: dict[str, str] = {}
        factor_label = _format_factor_label(self._selected_factor)

        if user_input is not None:
            pass_code = user_input["code"]
            async with self._async_client() as client:
                try:
                    self._tokens = await client.async_verify_mfa_code(
                        self._state_token, self._selected_factor, pass_code
                    )
                except EngieAuthError:
                    errors["base"] = "invalid_mfa_code"
                except EngieApiError:
                    errors["base"] = "cannot_connect"
                except Exception as err:
                    _LOGGER.exception("Unexpected MFA verification error: %s", err)
                    errors["base"] = "unknown"
                else:
                    return await self._async_after_tokens()

        schema = vol.Schema({vol.Required("code"): str})
        return self.async_show_form(
            step_id="mfa_code",
            data_schema=schema,
            errors=errors,
            description_placeholders={"factor": factor_label},
        )

    async def _async_after_tokens(self) -> ConfigFlowResult:
        """Fetch contracts or update reauth entry once OAuth tokens are acquired."""
        if self._reauth_entry is not None:
            new_data = dict(self._reauth_entry.data)
            new_data[CONF_ACCESS_TOKEN] = self._tokens.get("access_token")
            new_data[CONF_REFRESH_TOKEN] = self._tokens.get("refresh_token")
            new_data[CONF_ID_TOKEN] = self._tokens.get("id_token")
            new_data[CONF_TOKEN_EXPIRES_AT] = self._tokens.get("token_expires_at", 0.0)
            self.hass.config_entries.async_update_entry(self._reauth_entry, data=new_data)
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        async with self._async_client() as client:
            try:
                await client.async_init_session_cookies()
                info_users = await client.async_get_info_users()
            except EngieApiError as err:
                _LOGGER.error("Failed to fetch ENGIE contracts after login: %s", err)
                return self.async_abort(reason="cannot_connect")

        jwt_id_personne = extract_id_personne_from_jwt(self._tokens.get("access_token"))
        choices: dict[str, dict[str, Any]] = {}
        list_bp = info_users.get("infosUsers") or info_users.get("listBp") or []
        for bp in list_bp:
            ref_bp = str(bp.get("refBP") or "")
            id_personne = str(bp.get("idPersonne") or "") or jwt_id_personne
            for cc in bp.get("listeCC") or []:
                num_cc = str(cc.get("numeroCC") or "")
                if not num_cc:
                    continue
                addr = cc.get("adresse") or {}
                cp = str(addr.get("cp") or "")
                ville = str(addr.get("ville") or "")
                energies = sorted(
                    {
                        str(c.get("type") or c.get("typeEnergie") or "").upper()
                        for c in (cc.get("listeContrat") or [])
                        if c.get("type") or c.get("typeEnergie")
                    }
                )
                label = f"{num_cc} - {addr.get('libelleVoie', '')} {ville} ({'/'.join(energies) or 'ENGIE'})".strip()
                choices[num_cc] = {
                    "label": label,
                    "ref_bp": ref_bp,
                    "id_personne": id_personne,
                    "compte_contrat": num_cc,
                    "code_postal": cp,
                }

        if not choices:
            return self.async_abort(reason="no_contracts")

        self._contracts_choices = choices
        if len(choices) == 1:
            only_cc = next(iter(choices.values()))
            return await self._async_create_cc_entry(only_cc)

        return await self.async_step_contract()

    async def async_step_contract(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: Let user pick the CompteContrat if multiple exist."""
        options = {cc: info["label"] for cc, info in self._contracts_choices.items()}

        if user_input is not None:
            selected = self._contracts_choices[user_input[CONF_COMPTE_CONTRAT]]
            return await self._async_create_cc_entry(selected)

        schema = vol.Schema({vol.Required(CONF_COMPTE_CONTRAT): vol.In(options)})
        return self.async_show_form(step_id="contract", data_schema=schema)

    async def _async_create_cc_entry(self, cc_info: dict[str, Any]) -> ConfigFlowResult:
        """Create the Home Assistant ConfigEntry for the chosen CompteContrat."""
        num_cc = cc_info["compte_contrat"]
        await self.async_set_unique_id(f"engie_{num_cc}")
        self._abort_if_unique_id_configured()

        title = f"ENGIE ({num_cc})"
        data = {
            CONF_USERNAME: self._username,
            CONF_ACCESS_TOKEN: self._tokens.get("access_token"),
            CONF_REFRESH_TOKEN: self._tokens.get("refresh_token"),
            CONF_ID_TOKEN: self._tokens.get("id_token"),
            CONF_TOKEN_EXPIRES_AT: self._tokens.get("token_expires_at", 0.0),
            CONF_REF_BP: cc_info["ref_bp"],
            CONF_COMPTE_CONTRAT: num_cc,
            CONF_CODE_POSTAL: cc_info["code_postal"],
            CONF_ID_PERSONNE: cc_info["id_personne"],
            CONF_IMPORT_HISTORY: self._import_history,
        }
        return self.async_create_entry(title=title, data=data)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle re-authentication when OAuth refresh_token expires."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        self._username = entry_data.get(CONF_USERNAME, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt credentials for reauth without import_history."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            password = user_input["password"]
            result = await self._async_attempt_login(self._username, password, errors)
            if result is not None:
                return result

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=self._username): str,
                vol.Required("password"): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler for DomoLink ENGIE."""
        return EngieOptionsFlowHandler()


class EngieOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle DomoLink ENGIE polling interval and ZLinky_TIC real-time options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage polling intervals and ZLinky_TIC sensor mapping."""
        if user_input is not None:
            cleaned = dict(user_input)
            for k in (CONF_ZLINKY_DEVICE_PREFIX, *ZLINKY_SENSOR_KEYS):
                if k in cleaned:
                    cleaned[k] = str(cleaned[k] or "").strip()
            return self.async_create_entry(title="", data=cleaned)

        opts = getattr(self.config_entry, "options", None) or {}
        current_scan = int(opts.get(CONF_SCAN_INTERVAL, 120))
        current_billing = int(opts.get(CONF_BILLING_INTERVAL, 6))
        zlinky_enabled = bool(opts.get(CONF_ZLINKY_ENABLED, True))

        zlinky_info = detect_and_resolve_zlinky(getattr(self, "hass", None), options=opts)
        resolved = zlinky_info.get("resolved") or {}
        default_prefix = str(opts.get(CONF_ZLINKY_DEVICE_PREFIX) or zlinky_info.get("device_prefix") or "")

        schema_dict: dict[Any, Any] = {
            vol.Optional(CONF_SCAN_INTERVAL, default=current_scan): vol.All(
                int, vol.Range(min=5, max=360)
            ),
            vol.Optional(CONF_BILLING_INTERVAL, default=current_billing): vol.All(
                int, vol.Range(min=1, max=24)
            ),
            vol.Optional(CONF_ZLINKY_ENABLED, default=zlinky_enabled): bool,
            vol.Optional(CONF_ZLINKY_DEVICE_PREFIX, default=default_prefix): str,
        }
        for key in ZLINKY_SENSOR_KEYS:
            default_eid = str(opts.get(key) or resolved.get(key) or "")
            schema_dict[vol.Optional(key, default=default_eid)] = str

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))
