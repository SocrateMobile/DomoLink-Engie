"""Async API Client for ENGIE Particuliers (Okta OIDC PKCE + DVMobile + ERL)."""
from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
import hashlib
import json
import logging
import secrets
import ssl
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

from .const import (
    DEMANDEUR_COOKIE,
    DEMANDEUR_DVMOBILE,
    DEMANDEUR_ERL,
    DGP_CLIENT_ID,
    DGP_OAUTH_AUTHORIZE_URL,
    DGP_OAUTH_TOKEN_URL,
    DGP_REDIRECT_URI,
    DGP_SCOPES,
    ENGIE_BASE_URL,
    ENGIE_HTTP_HEADER_LIMIT,
    ENGIE_USER_AGENT,
    OKTA_AUTHN_URL,
    PAS_TEMPS_JOUR,
    PATH_ACCORD_ELEC,
    PATH_ACCORD_GAZ,
    PATH_CONSUMPTION_VARIATION,
    PATH_COURBE_REPARTITION_USAGES,
    PATH_CREATION_COOKIE,
    PATH_ENGAGEMENT_SOLDE,
    PATH_ERL_AVG_POWER,
    PATH_ERL_HOME,
    PATH_ERL_MAX_POWER,
    PATH_ESTIMATION_ELEC,
    PATH_ESTIMATION_GAZ,
    PATH_HISTO_CARBONE_ELEC,
    PATH_HISTO_ELEC_HEURES,
    PATH_HISTO_ELEC_MOIS,
    PATH_HISTO_GAZ_MOIS,
    PATH_INFO_CHEQUE_ENERGIE,
    PATH_INFO_USERS,
    PATH_LISTE_FACTURE,
    PATH_LISTE_PAIEMENTS,
    PATH_MA_CONSO,
    PATH_PLAN_MENS,
    PATH_PROFIL_CLIENT,
    PATH_RELEVE,
    PATH_REPARTITION_HPHC_JOUR,
    PATH_REPARTITION_USAGES_DUAL,
    PATH_SERVICES_OPTIONS,
    PATH_SOLDE_A_PAYER,
)

_LOGGER = logging.getLogger(__name__)


class EngieApiError(Exception):
    """Generic ENGIE API error."""


class EngieAuthError(EngieApiError):
    """Authentication or authorization failure."""


class EngieEndpointUnauthorized(EngieApiError):
    """Endpoint-specific 403 Unauthorized (gateway/ACL restriction, token still valid)."""


class EngieMfaRequired(EngieApiError):
    """MFA challenge required to complete Okta login."""

    def __init__(
        self,
        state_token: str,
        factors: list[dict[str, Any]],
        user_profile: dict[str, Any] | None = None,
    ) -> None:
        super().__init__("MFA required")
        self.state_token = state_token
        self.factors = factors
        self.user_profile = user_profile or {}


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a compliant OAuth2 PKCE (code_verifier, code_challenge) pair."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def safe_float(val: Any) -> float | None:
    """Safely convert French/English numeric strings or numbers to float."""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).strip().replace(" ", "").replace(",", "."))
    except (ValueError, TypeError):
        return None


def determine_user_type(
    has_elec: bool,
    has_gaz: bool,
    elec_comm: bool = True,
    gaz_comm: bool = True,
) -> str:
    """Build the exact userType code from ConsoRepositoryImpl.getUserType."""
    if has_elec and has_gaz:
        if elec_comm and gaz_comm:
            return "DUALCO"
        if elec_comm:
            return "DUALELECCO"
        if gaz_comm:
            return "DUALGAZCO"
        return "DUALNC"
    if has_gaz:
        return "GAZCO" if gaz_comm else "GAZNC"
    return "ELECCO" if elec_comm else "ELECNC"


def _build_ssl_context() -> ssl.SSLContext | bool:
    """Build SSL context using certifi CA bundle (safe to run in executor)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return True


def create_engie_aiohttp_session(
    ssl_ctx: ssl.SSLContext | bool | None = None,
) -> aiohttp.ClientSession:
    """Create an aiohttp session supporting ENGIE's oversized (>8KB) CSP headers."""
    if ssl_ctx is None:
        try:
            from homeassistant.util.ssl import get_default_context

            ssl_ctx = get_default_context()
        except Exception:
            ssl_ctx = _build_ssl_context()

    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    return aiohttp.ClientSession(
        connector=connector,
        cookie_jar=aiohttp.DummyCookieJar(),
        max_line_size=ENGIE_HTTP_HEADER_LIMIT,
        max_field_size=ENGIE_HTTP_HEADER_LIMIT,
    )


async def async_create_engie_aiohttp_session(hass: Any = None) -> aiohttp.ClientSession:
    """Asynchronously create an aiohttp session without blocking the HA event loop."""
    ssl_ctx: ssl.SSLContext | bool | None = None
    try:
        from homeassistant.util.ssl import get_default_context

        ssl_ctx = get_default_context()
    except Exception:
        if hass is not None and hasattr(hass, "async_add_executor_job"):
            ssl_ctx = await hass.async_add_executor_job(_build_ssl_context)
        else:
            ssl_ctx = _build_ssl_context()
    return create_engie_aiohttp_session(ssl_ctx=ssl_ctx)


def extract_id_personne_from_jwt(access_token: str | None) -> str:
    """Extract XDATA_DGP person identifier from Okta access_token claims if present."""
    if not access_token or "." not in access_token:
        return ""
    try:
        payload_b64 = access_token.split(".")[1]
        payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
        for ref in claims.get("listeReferenceSI") or []:
            if isinstance(ref, dict) and ref.get("application") == "XDATA_DGP" and ref.get("identifiantSi"):
                return str(ref["identifiantSi"])
    except Exception:
        return ""
    return ""


class EngieApiClient:
    """Asynchronous client for ENGIE Particuliers mobile APIs."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str | None = None,
        refresh_token: str | None = None,
        id_token: str | None = None,
        token_expires_at: float = 0.0,
        on_token_refresh: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._session = session
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.id_token = id_token
        self.token_expires_at = token_expires_at
        self._on_token_refresh = on_token_refresh
        self._cookies: dict[str, str] = {}
        self._ref_bp: str = ""
        self._compte_contrat: str = ""
        self._id_personne: str = ""

    def set_account_context(
        self,
        ref_bp: str = "",
        compte_contrat: str = "",
        id_personne: str = "",
    ) -> None:
        """Persist BusinessPartner, CompteContrat, and idPersonne for EngieCookieJar."""
        if ref_bp:
            self._ref_bp = ref_bp
            self._cookies["Cel_Bp"] = ref_bp
        if compte_contrat:
            self._compte_contrat = compte_contrat
            self._cookies["Cel_Cc"] = compte_contrat
        if id_personne:
            self._id_personne = id_personne
            self._cookies["idPersonne"] = id_personne

    def _capture_set_cookies(self, resp: Any) -> None:
        """Store raw Set-Cookie pairs verbatim (avoids SimpleCookie quoting issues)."""
        headers = getattr(resp, "headers", None)
        if not headers or not hasattr(headers, "getall"):
            return
        try:
            raw_list = headers.getall("Set-Cookie", [])
        except Exception:
            return
        for sc in raw_list:
            if "=" not in sc:
                continue
            pair = sc.split(";", 1)[0].strip()
            if "=" in pair:
                name, value = pair.split("=", 1)
                self._cookies[name.strip()] = value.strip()

    def _build_cookie_header(self) -> str:
        """Build Cookie header replicating EngieCookieJar.loadForRequest."""
        merged = dict(self._cookies)
        if self.access_token:
            merged["access_token"] = self.access_token
        if self._compte_contrat:
            merged["Cel_Cc"] = self._compte_contrat
        if self._ref_bp:
            merged["Cel_Bp"] = self._ref_bp
        if self._id_personne:
            merged["idPersonne"] = self._id_personne
        return "; ".join(f"{k}={v}" for k, v in merged.items() if v)

    def _default_headers(
        self,
        is_erl: bool = False,
        is_cookie_init: bool = False,
        include_auth: bool = True,
    ) -> dict[str, str]:
        """Build headers replicating AuthInterceptor.addParams (ClientIDCookie mode)."""
        demandeur = (
            DEMANDEUR_COOKIE
            if is_cookie_init
            else (DEMANDEUR_ERL if is_erl else DEMANDEUR_DVMOBILE)
        )
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "User-Agent": ENGIE_USER_AGENT,
            "demandeur": demandeur,
        }
        if include_auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        cookie_hdr = self._build_cookie_header()
        if cookie_hdr:
            headers["Cookie"] = cookie_hdr
        return headers

    async def async_init_session_cookies(
        self,
        ref_bp: str = "",
        compte_contrat: str = "",
        id_personne: str = "",
        force: bool = False,
    ) -> None:
        """Initialize ClientIDCookie via /digital-common-ws/api-management/creation-cookie."""
        if not id_personne and self.access_token:
            id_personne = extract_id_personne_from_jwt(self.access_token)
        self.set_account_context(ref_bp=ref_bp, compte_contrat=compte_contrat, id_personne=id_personne)

        if self._cookies.get("ClientIDCookie") and not force:
            return
        if not self.access_token:
            return

        headers = self._default_headers(is_cookie_init=True)
        try:
            async with self._session.post(
                f"{ENGIE_BASE_URL}{PATH_CREATION_COOKIE}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                self._capture_set_cookies(resp)
                if resp.status >= 400:
                    _LOGGER.debug("creation-cookie returned %s", resp.status)
        except (aiohttp.ClientError, TimeoutError, TypeError, AttributeError) as err:
            _LOGGER.debug("creation-cookie notice: %s", err)

    async def async_login_start(self, username: str, password: str) -> dict[str, Any]:
        """Authenticate username/password via Okta Classic AuthN."""
        payload = {"username": username.strip(), "password": password}
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": ENGIE_USER_AGENT,
        }
        try:
            async with self._session.post(
                OKTA_AUTHN_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EngieApiError(f"Erreur réseau Okta: {err}") from err

        if resp.status == 401 or data.get("errorCode") in ("E0000004", "E0000011"):
            raise EngieAuthError(data.get("errorSummary") or "Identifiant ou mot de passe incorrect.")
        if resp.status == 403 or data.get("errorCode") in ("E0000006", "E0000069"):
            raise EngieAuthError("Compte verrouillé ou temporairement bloqué par Okta.")
        if resp.status >= 400:
            raise EngieApiError(data.get("errorSummary") or f"Erreur Okta ({resp.status})")

        status = data.get("status")
        if status == "SUCCESS":
            session_token = data.get("sessionToken")
            if not session_token:
                raise EngieAuthError("Jeton de session Okta manquant.")
            return await self._async_exchange_pkce_code(session_token)

        if status in ("MFA_REQUIRED", "MFA_CHALLENGE"):
            embedded = data.get("_embedded") or {}
            factors = embedded.get("factors") or []
            user_profile = (embedded.get("user") or {}).get("profile") or {}
            raise EngieMfaRequired(
                state_token=data.get("stateToken", ""),
                factors=factors,
                user_profile=user_profile,
            )

        raise EngieApiError(f"Statut d'authentification Okta inattendu: {status}")

    async def async_send_mfa_challenge(self, state_token: str, factor: dict[str, Any]) -> str:
        """Request Okta to send an SMS or Email verification code for the chosen factor."""
        verify_url = ((factor.get("_links") or {}).get("verify") or {}).get("href")
        if not verify_url:
            factor_id = factor.get("id")
            verify_url = f"{OKTA_AUTHN_URL}/factors/{factor_id}/verify"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": ENGIE_USER_AGENT,
        }
        try:
            async with self._session.post(
                verify_url,
                json={"stateToken": state_token},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EngieApiError(f"Erreur envoi défi MFA: {err}") from err

        if resp.status >= 400:
            raise EngieApiError(data.get("errorSummary") or f"Erreur MFA ({resp.status})")

        return data.get("stateToken") or state_token

    async def async_verify_mfa_code(
        self,
        state_token: str,
        factor: dict[str, Any],
        pass_code: str,
    ) -> dict[str, Any]:
        """Verify the OTP code entered by the user and exchange sessionToken for OAuth tokens."""
        verify_url = ((factor.get("_links") or {}).get("verify") or {}).get("href")
        if not verify_url:
            factor_id = factor.get("id")
            verify_url = f"{OKTA_AUTHN_URL}/factors/{factor_id}/verify"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": ENGIE_USER_AGENT,
        }
        try:
            async with self._session.post(
                verify_url,
                json={"stateToken": state_token, "passCode": pass_code.strip()},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EngieApiError(f"Erreur validation code MFA: {err}") from err

        if resp.status >= 400 or data.get("status") != "SUCCESS":
            err_msg = data.get("errorSummary") or "Code MFA invalide ou expiré."
            raise EngieAuthError(err_msg)

        session_token = data.get("sessionToken")
        if not session_token:
            raise EngieAuthError("Aucun sessionToken renvoyé après validation MFA.")

        return await self._async_exchange_pkce_code(session_token)

    async def _async_exchange_pkce_code(self, session_token: str) -> dict[str, Any]:
        """Perform OAuth2 Authorization Code + PKCE exchange without browser."""
        verifier, challenge = generate_pkce_pair()
        state = secrets.token_urlsafe(16)
        nonce = secrets.token_urlsafe(16)

        auth_params = {
            "client_id": DGP_CLIENT_ID,
            "redirect_uri": DGP_REDIRECT_URI,
            "response_type": "code",
            "response_mode": "query",
            "scope": DGP_SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "sessionToken": session_token,
        }

        current_url: str = DGP_OAUTH_AUTHORIZE_URL
        current_params: dict[str, str] | None = auth_params
        auth_code: str | None = None

        for _ in range(5):
            async with self._session.get(
                current_url,
                params=current_params,
                headers={"User-Agent": ENGIE_USER_AGENT},
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                location = resp.headers.get("Location", "")
                if not location:
                    body = await resp.text()
                    raise EngieAuthError(f"Échec redirection OAuth2 ({resp.status}): {body[:200]}")

                if location.startswith(DGP_REDIRECT_URI) or "code=" in location:
                    parsed = urlparse(location)
                    qs = parse_qs(parsed.query)
                    if "error" in qs:
                        raise EngieAuthError(
                            qs.get("error_description", qs["error"])[0]
                        )
                    codes = qs.get("code")
                    if codes:
                        auth_code = codes[0]
                        break

                current_url = location
                current_params = None

        if not auth_code:
            raise EngieAuthError("Code d'autorisation OAuth2 introuvable.")

        token_data = {
            "grant_type": "authorization_code",
            "client_id": DGP_CLIENT_ID,
            "redirect_uri": DGP_REDIRECT_URI,
            "code": auth_code,
            "code_verifier": verifier,
        }
        return await self._async_post_token(token_data)

    async def async_refresh_tokens(self) -> dict[str, Any]:
        """Silently refresh OAuth2 access_token using the stored refresh_token."""
        if not self.refresh_token:
            raise EngieAuthError("Aucun refresh_token disponible, ré-authentification requise.")

        token_data = {
            "grant_type": "refresh_token",
            "client_id": DGP_CLIENT_ID,
            "refresh_token": self.refresh_token,
            "scope": DGP_SCOPES,
        }
        tokens = await self._async_post_token(token_data)
        if self._on_token_refresh:
            await self._on_token_refresh(tokens)
        return tokens

    async def _async_post_token(self, form_data: dict[str, str]) -> dict[str, Any]:
        """Exchange authorization code or refresh token at DGP token endpoint."""
        try:
            async with self._session.post(
                DGP_OAUTH_TOKEN_URL,
                data=form_data,
                headers={
                    "Accept": "application/json",
                    "User-Agent": ENGIE_USER_AGENT,
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EngieApiError(f"Erreur réseau échange token: {err}") from err

        if resp.status >= 400 or "access_token" not in data:
            err_msg = data.get("error_description") or data.get("error") or f"HTTP {resp.status}"
            if resp.status in (400, 401):
                raise EngieAuthError(f"Jeton expiré ou révoqué: {err_msg}")
            raise EngieApiError(f"Erreur token OAuth: {err_msg}")

        expires_in = int(data.get("expires_in", 3600))
        self.access_token = data["access_token"]
        if data.get("refresh_token"):
            self.refresh_token = data["refresh_token"]
        if data.get("id_token"):
            self.id_token = data["id_token"]
        self.token_expires_at = time.time() + max(60, expires_in - 60)

        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "id_token": self.id_token,
            "token_expires_at": self.token_expires_at,
        }

    async def _async_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        is_erl: bool = False,
        _retry: bool = True,
    ) -> Any:
        """Execute authenticated request against particuliers.engie.fr with transparent refresh."""
        if not self.access_token or time.time() >= self.token_expires_at:
            if self.refresh_token:
                await self.async_refresh_tokens()
            elif not self.access_token:
                raise EngieAuthError("Session non authentifiée.")

        if path != PATH_CREATION_COOKIE and not self._cookies.get("ClientIDCookie"):
            await self.async_init_session_cookies()

        url = f"{ENGIE_BASE_URL}{path}"
        headers = self._default_headers(is_erl=is_erl)

        clean_params = {k: str(v) for k, v in (params or {}).items() if v is not None}

        try:
            async with self._session.request(
                method,
                url,
                params=clean_params or None,
                json=json_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                self._capture_set_cookies(resp)

                if resp.status == 401 and _retry and self.refresh_token:
                    _LOGGER.debug("ENGIE API %s returned 401, refreshing token & cookies...", path)
                    await self.async_refresh_tokens()
                    await self.async_init_session_cookies(force=True)
                    return await self._async_request(
                        method,
                        path,
                        params=params,
                        json_body=json_body,
                        is_erl=is_erl,
                        _retry=False,
                    )

                if resp.status == 403:
                    text = await resp.text()
                    if "SEC_KO_EXPIRATION" in text and _retry:
                        _LOGGER.debug("ENGIE API %s returned SEC_KO_EXPIRATION, renewing session cookies...", path)
                        if path != PATH_INFO_USERS:
                            await self.async_get_info_users()
                        elif self.refresh_token:
                            await self.async_refresh_tokens()
                        await self.async_init_session_cookies(force=True)
                        return await self._async_request(
                            method,
                            path,
                            params=params,
                            json_body=json_body,
                            is_erl=is_erl,
                            _retry=False,
                        )
                    if '"Unauthorized"' in text and path not in (PATH_INFO_USERS, PATH_RELEVE):
                        raise EngieEndpointUnauthorized(f"Endpoint restreint ({path}): {text[:120]}")
                    raise EngieAuthError(f"Accès refusé ({resp.status}): {text[:200]}")

                if resp.status == 401:
                    text = await resp.text()
                    raise EngieAuthError(f"Accès refusé ({resp.status}): {text[:200]}")

                if resp.status >= 400:
                    text = await resp.text()
                    raise EngieApiError(f"Erreur API {path} ({resp.status}): {text[:200]}")

                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EngieApiError(f"Erreur réseau sur {path}: {err}") from err

    # --- High-level Account & Contract Methods ---

    async def async_get_info_users(self) -> dict[str, Any]:
        """Fetch Business Partners, CompteContrats, and Energy Contracts."""
        res = await self._async_request("GET", PATH_INFO_USERS)
        return res if isinstance(res, dict) else {}

    async def async_get_profil_client(self, ref_bp: str, cc: str) -> dict[str, Any]:
        """Fetch customer profile details."""
        res = await self._async_request(
            "GET", PATH_PROFIL_CLIENT, params={"refBP": ref_bp, "compteContrat": cc}
        )
        return res if isinstance(res, dict) else {}

    async def async_get_releve(self, ref_bp: str, cc: str) -> dict[str, Any]:
        """Fetch Linky/Gazpar meter info and latest indexes."""
        res = await self._async_request(
            "GET", PATH_RELEVE, params={"refBP": ref_bp, "compteContrat": cc}
        )
        return res if isinstance(res, dict) else {}

    async def async_get_solde(self, ref_bp: str, cc: str) -> dict[str, Any]:
        """Fetch current balance due and next invoice date."""
        res = await self._async_request(
            "GET", PATH_SOLDE_A_PAYER, params={"refBP": ref_bp, "compteContrat": cc}
        )
        return res if isinstance(res, dict) else {}

    async def async_get_plan_mens(self, ref_bp: str, cc: str) -> dict[str, Any]:
        """Fetch monthly payment schedule."""
        res = await self._async_request(
            "GET", PATH_PLAN_MENS, params={"refBP": ref_bp, "compteContrat": cc}
        )
        return res if isinstance(res, dict) else {}

    async def async_get_factures(self, ref_bp: str, cc: str) -> dict[str, Any]:
        """Fetch invoices list."""
        res = await self._async_request(
            "GET", PATH_LISTE_FACTURE, params={"refBP": ref_bp, "compteContrat": cc}
        )
        return res if isinstance(res, dict) else {}

    async def async_get_accords(self, ref_bp: str, cc: str) -> dict[str, Any]:
        """Fetch Linky/Gazpar collection consent status."""
        elec: dict[str, Any] = {}
        gaz: dict[str, Any] = {}
        try:
            elec = await self._async_request(
                "GET", PATH_ACCORD_ELEC, params={"refBP": ref_bp, "compteContrat": cc}
            )
        except EngieApiError:
            pass
        try:
            gaz = await self._async_request(
                "GET", PATH_ACCORD_GAZ, params={"refBP": ref_bp, "compteContrat": cc}
            )
        except EngieApiError:
            pass
        return {"elec": elec, "gaz": gaz}

    # --- Consumption, Tariffs, Carbon & History ---

    async def async_get_ma_conso(
        self,
        ref_bp: str,
        cc: str,
        code_postal: str,
        user_type: str,
        start_ms: int,
        end_ms: int,
        pas_temps: str = PAS_TEMPS_JOUR,
    ) -> dict[str, Any]:
        """Fetch electricity & gas consumption history over a date range."""
        params = {
            "refBP": ref_bp,
            "compteContrat": cc,
            "codePostal": code_postal,
            "userType": user_type,
            "ewePlus": "false",
            "startDate": start_ms,
            "endDate": end_ms,
            "pasTemps": pas_temps,
            "pilotage": "false",
        }
        res = await self._async_request("GET", PATH_MA_CONSO, params=params)
        return res if isinstance(res, dict) else {}

    async def async_get_histo_elec_mois(
        self,
        ref_bp: str,
        cc: str,
        code_postal: str,
        start_ms: int,
        end_ms: int,
        communicant: bool = True,
    ) -> dict[str, Any]:
        """Fetch monthly electricity consumption history."""
        params = {
            "communicant": str(communicant).lower(),
            "refBP": ref_bp,
            "compteContrat": cc,
            "codePostal": code_postal,
            "startDate": start_ms,
            "endDate": end_ms,
        }
        res = await self._async_request("GET", PATH_HISTO_ELEC_MOIS, params=params)
        return res if isinstance(res, dict) else {}

    async def async_get_histo_gaz_mois(
        self,
        ref_bp: str,
        cc: str,
        code_postal: str,
        start_ms: int,
        end_ms: int,
        communicant: bool = True,
    ) -> dict[str, Any]:
        """Fetch monthly gas consumption history."""
        params = {
            "communicant": str(communicant).lower(),
            "refBP": ref_bp,
            "compteContrat": cc,
            "codePostal": code_postal,
            "startDate": start_ms,
            "endDate": end_ms,
        }
        res = await self._async_request("GET", PATH_HISTO_GAZ_MOIS, params=params)
        return res if isinstance(res, dict) else {}

    async def async_get_histo_elec_heures(
        self,
        ref_bp: str,
        cc: str,
        start_ms: int,
        end_ms: int,
    ) -> dict[str, Any]:
        """Fetch hourly / half-hour electricity load curve."""
        params = {
            "refBP": ref_bp,
            "compteContrat": cc,
            "ewePlus": "false",
            "startDate": start_ms,
            "endDate": end_ms,
        }
        res = await self._async_request("GET", PATH_HISTO_ELEC_HEURES, params=params)
        return res if isinstance(res, dict) else {}

    async def async_get_repartition_hphc_jour(
        self, ref_bp: str, cc: str, start_ms: int, end_ms: int
    ) -> dict[str, Any]:
        """Fetch daily HP/HC breakdown."""
        params = {
            "refBP": ref_bp,
            "compteContrat": cc,
            "startDate": start_ms,
            "endDate": end_ms,
        }
        res = await self._async_request("GET", PATH_REPARTITION_HPHC_JOUR, params=params)
        return res if isinstance(res, dict) else {}

    async def async_get_estimation_elec(self, ref_bp: str, cc: str) -> dict[str, Any]:
        """Fetch current electricity kWh price & estimated monthly amount."""
        res = await self._async_request(
            "GET", PATH_ESTIMATION_ELEC, params={"refBP": ref_bp, "compteContrat": cc}
        )
        return res if isinstance(res, dict) else {}

    async def async_get_estimation_gaz(self, ref_bp: str, cc: str) -> dict[str, Any]:
        """Fetch current gas kWh price, conversion coefficient & estimated monthly amount."""
        res = await self._async_request(
            "GET", PATH_ESTIMATION_GAZ, params={"refBP": ref_bp, "compteContrat": cc}
        )
        return res if isinstance(res, dict) else {}

    async def async_get_histo_carbone(
        self, ref_bp: str, cc: str, id_personne: str, pdl: str, start_date_str: str
    ) -> dict[str, Any]:
        """Fetch monthly electricity carbon footprint."""
        params = {
            "refBP": ref_bp,
            "compteContrat": cc,
            "idPersonne": id_personne,
            "pdl": pdl,
            "startDate": start_date_str,
        }
        res = await self._async_request("GET", PATH_HISTO_CARBONE_ELEC, params=params)
        return res if isinstance(res, dict) else {}

    async def async_get_consumption_variation(
        self,
        ref_bp: str,
        cc: str,
        cp: str,
        type_energie: str = "DUAL",
        linky: bool = True,
        gazpar: bool = True,
    ) -> dict[str, Any]:
        """Fetch consumption variation percentage."""
        params = {
            "refBP": ref_bp,
            "compteContrat": cc,
            "typeEnergie": type_energie,
            "linky": str(linky).lower(),
            "gazpar": str(gazpar).lower(),
            "CP": cp,
        }
        res = await self._async_request("GET", PATH_CONSUMPTION_VARIATION, params=params)
        return res if isinstance(res, dict) else {}

    async def async_get_repartition_usages(
        self,
        ref_bp: str,
        cc: str,
        id_personne: str,
        start_ms: int,
        end_ms: int,
        energie: str = "DUAL",
    ) -> dict[str, Any]:
        """Fetch consumption breakdown by household usage category."""
        params = {
            "refBP": ref_bp,
            "compteContrat": cc,
            "idPersonne": id_personne,
            "startDate": start_ms,
            "endDate": end_ms,
            "energie": energie,
        }
        res = await self._async_request("GET", PATH_REPARTITION_USAGES_DUAL, params=params)
        return res if isinstance(res, dict) else {}

    async def async_get_repartition_usages_courbe(
        self,
        ref_bp: str,
        cc: str,
        user_type: str,
        type_offre: str,
        start_ms: int,
        end_ms: int,
    ) -> dict[str, Any]:
        """Fetch usage repartition from /courbe/getRepartitionUsages (live 200 OK)."""
        params = {
            "refBP": ref_bp,
            "compteContrat": cc,
            "userType": user_type,
            "typeOffre": type_offre,
            "startDate": start_ms,
            "endDate": end_ms,
        }
        res = await self._async_request("GET", PATH_COURBE_REPARTITION_USAGES, params=params)
        return res if isinstance(res, dict) else {}

    async def async_get_paiements(self, ref_bp: str, cc: str) -> dict[str, Any]:
        """Fetch payment history."""
        res = await self._async_request(
            "GET", PATH_LISTE_PAIEMENTS, params={"refBP": ref_bp, "compteContrat": cc}
        )
        return res if isinstance(res, dict) else {}

    async def async_get_engagement_solde(self, ref_bp: str, cc: str) -> dict[str, Any]:
        """Fetch customer engagement reward wallet balance."""
        res = await self._async_request(
            "GET", PATH_ENGAGEMENT_SOLDE, params={"refBP": ref_bp, "compteContrat": cc}
        )
        return res if isinstance(res, dict) else {}

    async def async_get_services_options(
        self, ref_bp: str, cc: str, type_energie: str = "DUAL", services_payants: bool = True
    ) -> dict[str, Any]:
        """Fetch subscribed services & options."""
        params = {
            "refBP": ref_bp,
            "compteContrat": cc,
            "typeEnergie": type_energie,
            "servicesPayants": str(services_payants).lower(),
        }
        res = await self._async_request("GET", PATH_SERVICES_OPTIONS, params=params)
        return res if isinstance(res, dict) else {}

    async def async_get_info_cheque_energie(self, ref_bp: str, cc: str) -> dict[str, Any]:
        """Fetch energy voucher (Chèque Énergie) info."""
        res = await self._async_request(
            "GET", PATH_INFO_CHEQUE_ENERGIE, params={"refBP": ref_bp, "compteContrat": cc}
        )
        return res if isinstance(res, dict) else {}

    # --- ERL Live Gateway ---

    async def async_get_erl_live(self, cc: str) -> dict[str, Any]:
        """Fetch live ERL power metrics if customer has active ERL device."""
        home_path = PATH_ERL_HOME.format(compteContrat=cc)
        home_data = await self._async_request("GET", home_path, is_erl=True)
        if not isinstance(home_data, dict):
            return {}

        devices = home_data.get("devices") or []
        if not devices and isinstance(home_data.get("home"), dict):
            devices = home_data["home"].get("devices") or []
        if not devices:
            return {"home": home_data}

        dev = devices[0]
        device_id = str(dev.get("deviceId") or dev.get("id") or "")
        id_pc = str(dev.get("idPc") or home_data.get("idPc") or "")
        if not device_id:
            return {"home": home_data}

        avg_power: dict[str, Any] = {}
        max_power: dict[str, Any] = {}
        try:
            avg_path = PATH_ERL_AVG_POWER.format(cc=cc, deviceId=device_id)
            avg_power = await self._async_request(
                "GET", avg_path, params={"idPc": id_pc or None, "frequency": "MINUTE"}, is_erl=True
            )
        except EngieApiError:
            pass
        try:
            max_path = PATH_ERL_MAX_POWER.format(cc=cc, deviceId=device_id)
            max_power = await self._async_request(
                "GET", max_path, params={"idPc": id_pc or None}, is_erl=True
            )
        except EngieApiError:
            pass

        return {
            "home": home_data,
            "device_id": device_id,
            "avg_power": avg_power,
            "max_power": max_power,
        }
