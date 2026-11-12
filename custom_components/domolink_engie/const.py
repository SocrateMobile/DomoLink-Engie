"""Constants for DomoLink ENGIE Électricité & Gaz integration."""
from __future__ import annotations

DOMAIN = "domolink_engie"
PLATFORMS: list[str] = ["sensor", "binary_sensor"]

# Configuration Keys
CONF_USERNAME = "username"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ID_TOKEN = "id_token"
CONF_TOKEN_EXPIRES_AT = "token_expires_at"
CONF_REF_BP = "ref_bp"
CONF_COMPTE_CONTRAT = "compte_contrat"
CONF_CODE_POSTAL = "code_postal"
CONF_ID_PERSONNE = "id_personne"
CONF_IMPORT_HISTORY = "import_history"

# Default Polling Intervals
DEFAULT_SCAN_INTERVAL_SECONDS = 7200  # 2 hours for consumption & meter data
BILLING_REFRESH_SECONDS = 21600  # 6 hours for invoices / monthly plan
ERL_REFRESH_SECONDS = 300  # 5 minutes if ERL live device active

# Okta / OIDC Production Endpoints (from com.engie.particuliers v9.6.3)
OKTA_AUTHN_URL = "https://identite-prd.engie.fr/api/v1/authn"
DGP_OAUTH_AUTHORIZE_URL = "https://api.dgp.engie.fr/oauth/v1/authorize"
DGP_OAUTH_TOKEN_URL = "https://api.dgp.engie.fr/oauth/v1/token"
DGP_CLIENT_ID = "5xpLtGiEUhB5tkv8hjPASrfodOyAPOGJ"
DGP_REDIRECT_URI = "engie:/loginCallback"
DGP_SCOPES = "openid profile offline_access idb2c apihour:read apihour:write"

# ENGIE Gateway Endpoints & Headers (from ProdEnvironment & AuthInterceptor)
ENGIE_BASE_URL = "https://particuliers.engie.fr"
ENGIE_USER_AGENT = "Mozilla/9.6.3 (Linux; Android 14; Pixel 7 Build/UP1A)"
ENGIE_HTTP_HEADER_LIMIT = 32768
DIG_ID_CLIENT = "DVMOBILE"
DIG_SECRET_CLIENT = "aC2KcDe!"
DEMANDEUR_COOKIE = "PARTICULIERS_ANDROID"
DEMANDEUR_DVMOBILE = "MOBILE_ANDROID"
DEMANDEUR_ERL = "ECARE4ERL-ANDROID"

# DVMobile & ERL API Paths
PATH_CREATION_COOKIE = "/digital-common-ws/api-management/creation-cookie"
PATH_INFO_USERS = "/dvmobile-ws/api/private/espaceClient/getInfoUsers"
PATH_PROFIL_CLIENT = "/dvmobile-ws/api/private/profilClient/recupererProfilClient"
PATH_RELEVE = "/dvmobile-ws/api/private/releve/recupererDonneesReleveV2"
PATH_MA_CONSO = "/dvmobile-ws/api/private/courbe/maConso"
PATH_COURBE_REPARTITION_USAGES = "/dvmobile-ws/api/private/courbe/getRepartitionUsages"
PATH_HISTO_ELEC_MOIS = "/dvmobile-ws/api/private/maConso/v3/histoElecMois"
PATH_HISTO_GAZ_MOIS = "/dvmobile-ws/api/private/maConso/v3/histoGazMois"
PATH_HISTO_ELEC_HEURES = "/dvmobile-ws/api/private/maConso/v1/histoElecHeures"
PATH_REPARTITION_HPHC_JOUR = "/dvmobile-ws/api/private/courbe/v2/getRepartitionHeuresCreusesJour"
PATH_ESTIMATION_ELEC = "/dvmobile-ws/api/private/maConso/v1/estimationConsoElecMois"
PATH_ESTIMATION_GAZ = "/dvmobile-ws/api/private/maConso/v1/estimationConsoGazMois"
PATH_REPARTITION_USAGES_DUAL = "/dvmobile-ws/api/private/maConso/v1/repartitionUsagesDual"
PATH_HISTO_CARBONE_ELEC = "/dvmobile-ws/api/private/maConso/v1/histoCarboneElecMois"
PATH_CONSUMPTION_VARIATION = "/dvmobile-ws/api/private/espaceClient/getConsumptionVariation"
PATH_SERVICES_OPTIONS = "/dvmobile-ws/api/private/espaceClient/servicesEtOptions"
PATH_INFO_CHEQUE_ENERGIE = "/dvmobile-ws/api/private/espaceClient/getInfoChequeEnergie"
PATH_SOLDE_A_PAYER = "/dvmobile-ws/api/private/facture/soldeApayerV2"
PATH_PLAN_MENS = "/dvmobile-ws/api/private/facture/planMensV2"
PATH_LISTE_FACTURE = "/dvmobile-ws/api/private/infolistefacture/listeFacture"
PATH_LISTE_PAIEMENTS = "/dvmobile-ws/api/private/paiement/recupererlistepaiements"
PATH_ENGAGEMENT_SOLDE = "/dvmobile-ws/api/private/engagement/getSolde"
PATH_ACCORD_ELEC = "/dvmobile-ws/api/private/accordClient/v2/infoAccordElec"
PATH_ACCORD_GAZ = "/dvmobile-ws/api/private/accordClient/v2/infoAccordGaz"

# ERL Live Paths
PATH_ERL_HOME = "/ecare4erl-ws/api/private/users/{compteContrat}/homeV3"
PATH_ERL_AVG_POWER = "/ecare4erl-ws/api/private/v3/users/{cc}/devices/{deviceId}/metrics/OfftakeElecApparentPower/avgPower"
PATH_ERL_MAX_POWER = "/ecare4erl-ws/api/private/v3/users/{cc}/devices/{deviceId}/metrics/OfftakeElecApparentPower/maxPower"

# Granularities (`pasTemps`)
PAS_TEMPS_MOIS = "M"
PAS_TEMPS_JOUR = "J"
PAS_TEMPS_HEURE = "H"
PAS_TEMPS_DEMI_HEURE = "Demi heure"

# Services
SERVICE_IMPORT_HISTORY = "import_history"
