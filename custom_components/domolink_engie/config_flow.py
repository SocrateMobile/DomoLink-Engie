"""Config Flow for DomoLink ENGIE Électricité & Gaz."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

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
    DOMAIN,
)
from .engie_api import (
    EngieApiClient,
    EngieApiError,
    EngieAuthError,
    EngieMfaRequired,
    create_engie_aiohttp_session,
    extract_id_personne_from_jwt,
)

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
        self._contracts_choices: dict[str, dict[str, Any]] = {}
        self._client: EngieApiClient | None = None
        self._session: aiohttp.ClientSession | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None

    def _get_client(self) -> EngieApiClient:
        if self._session is None or self._session.closed:
            self._session = create_engie_aiohttp_session()
            self._client = EngieApiClient(self._session)
        elif self._client is None:
            self._client = EngieApiClient(self._session)
        return self._client

    async def _async_close_session(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._client = None

    async def async_remove(self) -> None:
        """Close the temporary HTTP session if the config flow is abandoned."""
        await self._async_close_session()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Email and Password authentication."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            password = user_input["password"]
            self._import_history = bool(user_input.get(CONF_IMPORT_HISTORY, True))
            client = self._get_client()

            try:
                self._tokens = await client.async_login_start(self._username, password)
            except EngieMfaRequired as mfa:
                self._state_token = mfa.state_token
                self._factors = [
                    f
                    for f in mfa.factors
                    if str(f.get("factorType", "")).lower() in ("sms", "email", "token:software:totp", "call")
                ] or mfa.factors

                if not self._factors:
                    errors["base"] = "no_mfa_factor"
                elif len(self._factors) == 1:
                    self._selected_factor = self._factors[0]
                    try:
                        if str(self._selected_factor.get("factorType", "")).lower() != "token:software:totp":
                            self._state_token = await client.async_send_mfa_challenge(
                                self._state_token, self._selected_factor
                            )
                        return await self.async_step_mfa_code()
                    except EngieApiError:
                        errors["base"] = "cannot_connect"
                else:
                    return await self.async_step_mfa_select()
            except EngieAuthError:
                errors["base"] = "invalid_auth"
            except EngieApiError:
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected ENGIE login error: %s", err)
                errors["base"] = "unknown"
            else:
                return await self._async_after_tokens()

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
    ) -> FlowResult:
        """Step 2a: Choose SMS or Email MFA factor when multiple are enrolled."""
        errors: dict[str, str] = {}
        factor_map = {str(f["id"]): f for f in self._factors if "id" in f}
        options = {fid: _format_factor_label(f) for fid, f in factor_map.items()}

        if user_input is not None:
            fid = user_input["factor_id"]
            self._selected_factor = factor_map[fid]
            client = self._get_client()
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
    ) -> FlowResult:
        """Step 2b: Verify the one-time passcode received by SMS/Email."""
        errors: dict[str, str] = {}
        factor_label = _format_factor_label(self._selected_factor)

        if user_input is not None:
            pass_code = user_input["code"]
            client = self._get_client()
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

    async def _async_after_tokens(self) -> FlowResult:
        """Fetch contracts or update reauth entry once OAuth tokens are acquired."""
        if self._reauth_entry is not None:
            new_data = dict(self._reauth_entry.data)
            new_data[CONF_ACCESS_TOKEN] = self._tokens.get("access_token")
            new_data[CONF_REFRESH_TOKEN] = self._tokens.get("refresh_token")
            new_data[CONF_ID_TOKEN] = self._tokens.get("id_token")
            new_data[CONF_TOKEN_EXPIRES_AT] = self._tokens.get("token_expires_at", 0.0)
            self.hass.config_entries.async_update_entry(self._reauth_entry, data=new_data)
            await self._async_close_session()
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        client = self._get_client()
        try:
            await client.async_init_session_cookies()
            info_users = await client.async_get_info_users()
        except EngieApiError as err:
            _LOGGER.error("Failed to fetch ENGIE contracts after login: %s", err)
            await self._async_close_session()
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
            await self._async_close_session()
            return self.async_abort(reason="no_contracts")

        self._contracts_choices = choices
        if len(choices) == 1:
            only_cc = next(iter(choices.values()))
            return await self._async_create_cc_entry(only_cc)

        return await self.async_step_contract()

    async def async_step_contract(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Let user pick the CompteContrat if multiple exist."""
        options = {cc: info["label"] for cc, info in self._contracts_choices.items()}

        if user_input is not None:
            selected = self._contracts_choices[user_input[CONF_COMPTE_CONTRAT]]
            return await self._async_create_cc_entry(selected)

        schema = vol.Schema({vol.Required(CONF_COMPTE_CONTRAT): vol.In(options)})
        return self.async_show_form(step_id="contract", data_schema=schema)

    async def _async_create_cc_entry(self, cc_info: dict[str, Any]) -> FlowResult:
        """Create the Home Assistant ConfigEntry for the chosen CompteContrat."""
        await self._async_close_session()
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

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle re-authentication when OAuth refresh_token expires."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        self._username = entry_data.get(CONF_USERNAME, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Prompt credentials for reauth."""
        return await self.async_step_user(user_input)

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler for DomoLink ENGIE."""
        return EngieOptionsFlowHandler(config_entry)


class EngieOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle DomoLink ENGIE polling interval options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage polling intervals for consumption & billing."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_scan = int(self._config_entry.options.get(CONF_SCAN_INTERVAL, 120))
        current_billing = int(self._config_entry.options.get(CONF_BILLING_INTERVAL, 6))
        schema = vol.Schema(
            {
                vol.Optional(CONF_SCAN_INTERVAL, default=current_scan): vol.All(
                    int, vol.Range(min=30, max=360)
                ),
                vol.Optional(CONF_BILLING_INTERVAL, default=current_billing): vol.All(
                    int, vol.Range(min=1, max=24)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
