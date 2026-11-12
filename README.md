# DomoLink ENGIE Électricité & Gaz (`domolink_engie`)

Intégration personnalisée Home Assistant pour suivre en détail votre consommation **Électricité (Linky)** et **Gaz (Gazpar)**, vos tarifs, votre facturation ENGIE Particuliers (France) et importer votre historique complet directement dans le **Tableau de bord Énergie** de Home Assistant.

## Fonctionnalités principales

- **Authentification Okta OIDC PKCE sécurisée** : gestion intégrée du code MFA (SMS / E-mail) **une seule fois à l'installation**, avec renouvellement silencieux automatique via `refresh_token` (`offline_access`).
- **Capteurs reconnus nativement par le Tableau de bord Énergie HA** :
  - **Électricité** : Index Linky Total / Heures Pleines / Heures Creuses (`kWh`, `ENERGY`, `TOTAL_INCREASING`), consommation veille et mensuelle, coût en `EUR`, tarif exact `EUR/kWh`, empreinte carbone, variation %.
  - **Gaz** : Index Gazpar (`m³`, `GAS`, `TOTAL_INCREASING`), consommation veille et mensuelle en **`m³`** ET **`kWh`**, coefficient de conversion Gaz, coût en `EUR`, tarif `EUR/kWh`.
  - **Compte & Facturation** : Solde à payer, mensualités globales / élec / gaz, date et montant de dernière facture, date prochaine facture, échéances restantes.
  - **ERL Linky temps réel** *(si équipé)* : puissance apparente max du jour (`VA`).
- **Historisation rétroactive automatique** : injection de jusqu'à 12–36 mois d'historique directement dans les statistiques du `recorder` Home Assistant (`domolink_engie.import_history`).

## Installation via HACS

1. Ajoutez ce dépôt en tant que dépôt personnalisé dans HACS (`Intégration`).
2. Installez **DomoLink ENGIE Électricité & Gaz**.
3. Redémarrez Home Assistant.
4. Dans **Paramètres → Appareils et services → Ajouter une intégration**, recherchez **DomoLink ENGIE** et saisissez vos identifiants ENGIE.
5. Configurez vos sources dans **Paramètres → Tableaux de bord → Énergie** en sélectionnant soit les capteurs d'index (`Index Électricité Total`, `Index Compteur Gaz`), soit les statistiques historiques `domolink_engie:...`.
