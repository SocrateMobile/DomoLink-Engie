# DomoLink ENGIE Électricité & Gaz (`domolink_engie`) — v1.3.0

Intégration personnalisée Home Assistant pour suivre en détail votre consommation **Électricité (Linky)** et **Gaz (Gazpar)**, vos tarifs, votre facturation ENGIE Particuliers (France), coupler un capteur **ZLinky_TIC (Zigbee2MQTT / ZHA / deCONZ / TIC)** en temps réel, et importer votre historique complet directement dans le **Tableau de bord Énergie** de Home Assistant.

## Fonctionnalités principales

- **Authentification Okta OIDC PKCE sécurisée** : gestion intégrée du code MFA (SMS / E-mail) **une seule fois à l'installation**, avec renouvellement silencieux automatique via `refresh_token` (`offline_access`).
- **Panneau latéral "DomoLink ENGIE" & Carte Lovelace `custom:domolink-engie-card`** :
  - **5 onglets interactifs** : *⚡ Moniteur Énergie*, *📊 Consommations (24h, 7J, 30J, 12M + comparatif N-1)*, *🥧 Répartition par usage*, *💶 Tarifs & Factures*, et *⚙️ Paramétrage & ZLinky_TIC*.
- **Intégration Temps Réel ZLinky_TIC (Zigbee2MQTT, ZHA, deCONZ/Dresden, Téléinfo TIC)** :
  - **Auto-détection intelligente** de votre module ZLinky_TIC et de ses 11 capteurs clés (`SINSTS` VA, `W`, `URMS1` V, `IRMS1` A, `EAST` Total kWh, `EASF01` HC kWh, `EASF02` HP kWh, `PTEC` période tarifaire HP/HC en cours, `SMAXSN` pic journalier VA, `PREF`/`ISOUSC` puissance souscrite kVA, `CCASN` courbe de charge).
  - **Sélection & surcharge manuelle** directement depuis l'onglet **⚙️ Paramétrage & ZLinky_TIC** du panneau latéral (ou via les options de l'intégration Home Assistant) avec autocomplétion des entités HA, normalisation automatique des unités (`Wh` → `kWh`, `A` → `kVA`) et calcul de la consommation du jour en direct.
- **Capteurs reconnus nativement par le Tableau de bord Énergie HA** :
  - **Électricité** : Index Linky Total / Heures Pleines / Heures Creuses (`kWh`, `ENERGY`, `TOTAL_INCREASING`), consommation veille (Totale, HP, HC et taux d'optimisation HC `%`) et mensuelle, coût en `EUR`, tarifs exacts Base / HP / HC (`EUR/kWh`), abonnement (`EUR`), température extérieure moyenne (`°C`), empreinte carbone, variation `%`.
  - **Gaz** : Index Gazpar (`m³`, `GAS`, `TOTAL_INCREASING`), consommation veille et mensuelle en **`m³`** ET **`kWh`**, coefficient de conversion Gaz, coût en `EUR`, tarif `EUR/kWh`, abonnement (`EUR`).
  - **Compte & Facturation** : Solde à payer, **Régularisation estimée en fin d'échéancier** (`EUR`), mensualités globales / élec / gaz, date et montant de dernière facture, date prochaine facture, échéances restantes.
  - **ERL Linky temps réel** *(si équipé)* : puissance apparente max du jour (`VA`) et puissance moyenne demi-heure (`VA`) avec rafraîchissement rapide (5 min).
- **Historisation rétroactive automatique sans discontinuité** : injection de jusqu'à 12–36 mois d'historique directement dans les statistiques du `recorder` Home Assistant (`domolink_engie.import_history`) avec ancrage cumulatif (`_async_get_prior_sum`) et déduplication horaire/journalière.

## Installation via HACS

1. Ajoutez ce dépôt en tant que dépôt personnalisé dans HACS (`Intégration`).
2. Installez **DomoLink ENGIE Électricité & Gaz**.
3. Redémarrez Home Assistant.
4. Dans **Paramètres → Appareils et services → Ajouter une intégration**, recherchez **DomoLink ENGIE** et saisissez vos identifiants ENGIE.
5. *(Optionnel)* Ouvrez le panneau **DomoLink ENGIE** dans la barre latérale → onglet **⚙️ Paramétrage & ZLinky_TIC** pour vérifier l'auto-détection de votre ZLinky_TIC ou personnaliser vos capteurs temps réel.
6. Configurez vos sources dans **Paramètres → Tableaux de bord → Énergie** en sélectionnant soit les capteurs d'index (`Index Électricité Total`, `Index Compteur Gaz`), soit les statistiques historiques `domolink_engie:...`.
