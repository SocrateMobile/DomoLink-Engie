/**
 * DomoLink ENGIE — Home Assistant Sidebar Panel (with ZLinky_TIC Auto-Detection & Live Telemetry)
 * Element: <domolink-engie-panel>
 * Version: 1.3.0
 */

import "./domolink-engie-card.js";

const PANEL_TAG = "domolink-engie-panel";

function esc(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmt(val, d = 2) {
  const n = Number(val);
  if (!Number.isFinite(n)) return "--";
  return n.toLocaleString("fr-FR", {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

class DomoLinkEngiePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._snapshot = null;
    this._activeTab = "moniteur";
    this._selectedEntryId = null;
    this._loading = false;
    this._toast = "";
    this._moniteurState = { tab: "linky", period: "daily", lockTabs: false };
    this._cardGen = {
      kind: "custom",
      title: "Moniteur Énergie",
      mode: "moniteur_full",
      default_tab: "linky",
      default_period: "daily",
      zlinky_device_prefix: "",
      power_sensor: "",
      daily_energy_sensor: "",
      theme: "domolink_dark",
      show_cost: true,
      show_annual_comparison: true,
      show_usages: true,
      show_billing: true,
    };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._loadSnapshot();
    } else {
      const preview = this.shadowRoot?.querySelector("domolink-engie-card");
      if (preview) preview.hass = hass;
      if (this._activeTab === "moniteur" && this._snapshot) {
        this._renderMoniteurInPlace();
      }
    }
  }

  set panel(_panel) {}
  set narrow(_narrow) {}

  _renderMoniteurInPlace() {
    const host = this.shadowRoot?.getElementById("moniteur-live-host");
    const acc = this._getCurrentAccount();
    if (!host || !acc || !window.DomoLinkEngieMoniteur) return;
    host.innerHTML = window.DomoLinkEngieMoniteur.renderMoniteurHTML(
      acc,
      this._hass,
      this._moniteurState,
      {},
      () => this._renderMoniteurInPlace()
    );
    host.querySelectorAll("[data-dlm-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._moniteurState.tab = btn.getAttribute("data-dlm-tab");
        this._renderMoniteurInPlace();
      });
    });
    host.querySelectorAll("[data-dlm-period]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._moniteurState.period = btn.getAttribute("data-dlm-period");
        this._renderMoniteurInPlace();
      });
    });
  }

  async _loadSnapshot() {
    if (!this._hass) return;
    this._loading = true;
    this._render();
    try {
      const res = await this._hass.callWS({ type: "domolink_engie/get_snapshot" });
      this._snapshot = res;
      if (!this._selectedEntryId && res?.accounts?.length) {
        this._selectedEntryId = res.accounts[0].entry_id;
      }
    } catch (err) {
      this._showToast(`Erreur chargement snapshot: ${err.message || err}`);
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _showToast(msg) {
    this._toast = msg;
    this._render();
    setTimeout(() => {
      this._toast = "";
      this._render();
    }, 3500);
  }

  _getCurrentAccount() {
    const accs = this._snapshot?.accounts || [];
    return accs.find((a) => a.entry_id === this._selectedEntryId) || accs[0] || null;
  }

  _buildLovelaceYaml(acc) {
    const g = this._cardGen;
    const cc = acc?.compte_contrat || "";
    if (g.kind === "native") {
      const ent = (k, fallback) => acc?.entities?.[k] || `sensor.engie_electricite_${cc}_${fallback}`;
      return [
        "type: vertical-stack",
        "cards:",
        "  - type: entities",
        `    title: ${g.title}`,
        "    entities:",
        `      - entity: ${ent("elec_monthly_conso", "consommation_electricite_mois")}`,
        `      - entity: ${ent("elec_monthly_cost", "cout_electricite_mois")}`,
        `      - entity: ${ent("elec_annual_conso", "consommation_electricite_annuelle")}`,
        `      - entity: ${acc?.entities?.["gaz_index_m3"] || `sensor.engie_gaz_${cc}_index_compteur_gaz`}`,
        `      - entity: ${acc?.entities?.["gaz_monthly_kwh"] || `sensor.engie_gaz_${cc}_consommation_gaz_mois_kwh`}`,
        `      - entity: ${acc?.entities?.["gaz_monthly_cost"] || `sensor.engie_gaz_${cc}_cout_gaz_mois`}`,
        `      - entity: ${acc?.entities?.["account_mensualite"] || `sensor.engie_compte_${cc}_mensualite_globale`}`,
        `      - entity: ${acc?.entities?.["account_solde"] || `sensor.engie_compte_${cc}_solde_a_payer`}`,
        "  - type: statistics-graph",
        "    title: Historique Énergie ENGIE",
        "    chart_type: bar",
        "    period: month",
        "    days_to_show: 365",
        "    entities:",
        `      - ${ent("elec_monthly_conso", "consommation_electricite_mois")}`,
        `      - ${acc?.entities?.["gaz_monthly_kwh"] || `sensor.engie_gaz_${cc}_consommation_gaz_mois_kwh`}`,
      ].join("\n");
    }

    const isMoniteurMode = String(g.mode || "").startsWith("moniteur_");
    return [
      "type: custom:domolink-engie-card",
      `title: "${g.title}"`,
      cc ? `compte_contrat: "${cc}"` : null,
      `mode: ${g.mode}`,
      isMoniteurMode ? `default_tab: ${g.default_tab || "linky"}` : null,
      isMoniteurMode ? `default_period: ${g.default_period || "daily"}` : null,
      isMoniteurMode && g.zlinky_device_prefix ? `zlinky_device_prefix: ${g.zlinky_device_prefix}` : null,
      isMoniteurMode && g.power_sensor ? `power_sensor: ${g.power_sensor}` : null,
      isMoniteurMode && g.daily_energy_sensor ? `daily_energy_sensor: ${g.daily_energy_sensor}` : null,
      !isMoniteurMode ? `theme: ${g.theme}` : null,
      !isMoniteurMode ? `show_cost: ${g.show_cost}` : null,
      !isMoniteurMode ? `show_annual_comparison: ${g.show_annual_comparison}` : null,
      !isMoniteurMode ? `show_usages: ${g.show_usages}` : null,
      !isMoniteurMode ? `show_billing: ${g.show_billing}` : null,
    ]
      .filter(Boolean)
      .join("\n");
  }

  _renderOverview(acc) {
    const ev = acc.elec_variation || {};
    const gv = acc.gaz_variation || {};
    const er = acc.elec_releve || {};
    const gr = acc.gaz_releve || {};
    const solde = acc.billing?.solde || {};
    const eng = acc.billing?.engagement || {};
    const zl = acc.zlinky || {};
    const zLive = zl.live || {};

    return `
      <div class="kpi-grid">
        ${acc.has_elec ? `
          <div class="card">
            <div class="card-top">
              <span class="kicker">⚡ Électricité · ${esc(ev.month_label || "Mois")}</span>
              <span class="pill ${er.compteurCommuniquant ? "ok" : "warn"}">${er.compteurCommuniquant ? "Linky OK" : "Non comm."}</span>
            </div>
            <div class="big" style="color:#38bdf8">${fmt(ev.month_kwh, 1)} <span>kWh</span></div>
            <div class="subrow">
              <span>Coût mois : <b>${fmt(ev.month_euro, 2)} €</b></span>
              <span>Var. : <b>${ev.month_variation_pct !== undefined && ev.month_variation_pct !== null ? `${ev.month_variation_pct > 0 ? "+" : ""}${fmt(ev.month_variation_pct, 0)}%` : "--"}</b></span>
            </div>
            <div class="divider"></div>
            <div class="subrow">
              <span>Cumul annuel : <b>${fmt(ev.year_kwh, 0)} kWh</b></span>
              <span>N-1 : <b>${fmt(ev.year_prev_kwh, 0)} kWh</b> (${ev.year_variation_pct > 0 ? "+" : ""}${fmt(ev.year_variation_pct, 0)}%)</span>
            </div>
            ${zl.detected ? `
              <div class="subrow" style="color:#34d399">
                <span>⚡ ZLinky Live : <b>${zLive.power_va !== null && zLive.power_va !== undefined ? `${fmt(zLive.power_va, 0)} VA` : "--"}</b></span>
                <span>Index Live : <b>${zLive.index_total_kwh !== null && zLive.index_total_kwh !== undefined ? `${fmt(zLive.index_total_kwh, 1)} kWh` : "--"}</b></span>
              </div>
            ` : ""}
            <div class="subrow muted">
              <span>PDL : ${esc(er.idPDL || "--")}</span>
              <span>Prochain relevé : ${esc(er.dateProchaineReleve || "--")}</span>
            </div>
          </div>
        ` : ""}

        ${acc.has_gaz ? `
          <div class="card">
            <div class="card-top">
              <span class="kicker">🔥 Gaz Naturel · ${esc(gv.month_label || "Mois")}</span>
              <span class="pill ${gr.compteurCommuniquant ? "ok" : "warn"}">${gr.compteurCommuniquant ? "Gazpar OK" : "Non comm."}</span>
            </div>
            <div class="big" style="color:#fb923c">${fmt(gv.month_kwh, 0)} <span>kWh</span></div>
            <div class="subrow">
              <span>Coût mois : <b>${fmt(gv.month_euro, 2)} €</b></span>
              <span>Index : <b>${fmt(gr.dernierIndex, 0)} m³</b> (${esc(gr.dateDernierIndex || "--")})</span>
            </div>
            <div class="divider"></div>
            <div class="subrow">
              <span>Cumul annuel : <b>${fmt(gv.year_kwh, 0)} kWh</b></span>
              <span>N-1 : <b>${fmt(gv.year_prev_kwh, 0)} kWh</b> (${gv.year_variation_pct > 0 ? "+" : ""}${fmt(gv.year_variation_pct, 0)}%)</span>
            </div>
            <div class="subrow muted">
              <span>PCE : ${esc(gr.idPDL || "--")}</span>
              <span>Prochain relevé : ${esc(gr.dateProchaineReleve || "--")}</span>
            </div>
          </div>
        ` : ""}

        <div class="card">
          <div class="card-top">
            <span class="kicker">💶 Budget & Statut</span>
            <span class="pill ${zl.detected || acc.client_erl ? "ok" : "neutral"}">${zl.detected ? "ZLinky_TIC Actif" : (acc.client_erl ? "ERL Actif" : "Standard")}</span>
          </div>
          <div class="big" style="color:#34d399">${fmt(solde.solde, 2)} <span>€ solde</span></div>
          <div class="subrow">
            <span>Prochaine facture : <b>${esc(solde.dateProchaineFacture || "--")}</b></span>
          </div>
          <div class="divider"></div>
          <div class="subrow">
            <span>Cagnotte engagement : <b>${fmt(eng.solde, 2)} €</b></span>
            <span>Tarif : <b>${esc(acc.type_tarif || "--")}</b></span>
          </div>
          <div class="subrow muted">
            <span>Commune : ${esc(acc.code_postal)} ${esc(acc.ville)}</span>
            <span>Synchro : ${acc.last_sync_utc ? new Date(acc.last_sync_utc).toLocaleTimeString("fr-FR") : "--"}</span>
          </div>
        </div>
      </div>
    `;
  }

  _renderHistoryUsages(acc) {
    const rep = acc.billing?.repartition_usages || {};
    const elecItems = rep.repartitionUsagesElec || [];
    const gazItems = rep.repartitionUsagesGaz || [];

    const renderList = (title, items) => `
      <div class="card">
        <div class="card-top"><span class="kicker">${esc(title)}</span></div>
        ${items.length ? items.map((it) => `
          <div class="bar-row">
            <div class="bar-label"><span>${esc(it.libelle)}</span><b>${fmt(it.valeur, 0)}%</b></div>
            <div class="bar-track"><div class="bar-fill" style="width:${Number(it.valeur) || 0}%;background:#${esc(it.couleur || "38bdf8")}"></div></div>
          </div>
        `).join("") : `<div class="muted">Aucune répartition disponible pour ce contrat.</div>`}
      </div>
    `;

    return `
      <div class="kpi-grid">
        ${renderList("⚡ Répartition Usages Électricité", elecItems)}
        ${renderList("🔥 Répartition Usages Gaz", gazItems)}
      </div>
      <div class="card" style="margin-top:16px">
        <div class="card-top"><span class="kicker">📈 Import Rétroactif Home Assistant Recorder</span></div>
        <p class="muted" style="margin:6px 0 14px">Injecte ou répare les statistiques long-terme dans le Tableau de bord Énergie de Home Assistant (sans doublons, avec continuité de somme cumulée).</p>
        <div class="actions">
          <button class="btn" data-import="6">Importer 6 mois</button>
          <button class="btn primary" data-import="12">Importer 12 mois</button>
          <button class="btn" data-import="24">Importer 24 mois</button>
        </div>
      </div>
    `;
  }

  _renderBilling(acc) {
    const plan = acc.billing?.plan_mens?.listPlanMensCourant || [];
    const factures = acc.billing?.factures?.listeFactures || [];
    const paiements = acc.billing?.paiements?.listePaiements || [];
    const services = acc.billing?.services_options?.services || [];

    return `
      <div class="kpi-grid">
        <div class="card">
          <div class="card-top"><span class="kicker">📅 Échéancier Mensualisation (${plan.length})</span></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Total</th><th>Élec / Gaz</th><th>Statut</th></tr></thead>
              <tbody>
                ${plan.slice(0, 8).map((p) => `
                  <tr>
                    <td>${esc(p.date)}</td>
                    <td><b>${fmt(p.montantMensualite, 2)} €</b></td>
                    <td>${fmt(p.detailMensualite?.elec, 2)} / ${fmt(p.detailMensualite?.gaz, 2)} €</td>
                    <td><span class="pill ${String(p.etatPaiement || "").includes("Régler") ? "warn" : "ok"}">${esc(p.etatPaiement)}</span></td>
                  </tr>
                `).join("") || `<tr><td colspan="4" class="muted">Aucun échéancier</td></tr>`}
              </tbody>
            </table>
          </div>
        </div>

        <div class="card">
          <div class="card-top"><span class="kicker">💳 Derniers Prélèvements (${paiements.length})</span></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Montant</th><th>Moyen</th><th>Statut</th></tr></thead>
              <tbody>
                ${paiements.slice(0, 8).map((p) => `
                  <tr>
                    <td>${esc(p.datePaiement)}</td>
                    <td><b>${fmt(p.montantTTC, 2)} €</b></td>
                    <td>${esc(p.moyenPaiement)}</td>
                    <td><span class="pill ok">${esc(p.statutPaiement)}</span></td>
                  </tr>
                `).join("") || `<tr><td colspan="4" class="muted">Aucun paiement</td></tr>`}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="kpi-grid" style="margin-top:16px">
        <div class="card">
          <div class="card-top"><span class="kicker">🧾 Factures (${factures.length})</span></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>N° Facture</th><th>Libellé</th><th>TTC</th><th>Statut</th></tr></thead>
              <tbody>
                ${factures.slice(0, 6).map((f) => `
                  <tr>
                    <td>${esc(f.dateFacture)}</td>
                    <td>${esc(f.numeroFacture)}</td>
                    <td>${esc(f.libelle)}</td>
                    <td><b>${fmt(f.montantTTC, 2)} €</b></td>
                    <td><span class="pill ok">${esc(f.statutPaiement)}</span></td>
                  </tr>
                `).join("") || `<tr><td colspan="5" class="muted">Aucune facture</td></tr>`}
              </tbody>
            </table>
          </div>
        </div>

        <div class="card">
          <div class="card-top"><span class="kicker">🧩 Services & Options ENGIE</span></div>
          ${services.map((s) => `
            <div class="subrow" style="padding:8px 0;border-bottom:1px solid rgba(148,163,184,0.1)">
              <span><b>${esc(s.libelle)}</b> <small class="muted">(${esc(s.prix || "inclus")})</small></span>
              <span class="pill ${s.souscriptionService ? "ok" : "neutral"}">${s.souscriptionService ? "Actif" : "Disponible"}</span>
            </div>
          `).join("") || `<div class="muted">Aucun service remonté.</div>`}
        </div>
      </div>
    `;
  }

  _renderGenerator(acc) {
    const yaml = this._buildLovelaceYaml(acc);
    const g = this._cardGen;

    return `
      <div class="kpi-grid">
        <div class="card">
          <div class="card-top"><span class="kicker">🎨 Configurateur Lovelace</span></div>
          <div class="form-grid">
            <label>Type de carte
              <select id="gen-kind">
                <option value="custom" ${g.kind === "custom" ? "selected" : ""}>Carte Dédiée (custom:domolink-engie-card)</option>
                <option value="native" ${g.kind === "native" ? "selected" : ""}>Pack Cartes Natives HA (sans custom card)</option>
              </select>
            </label>
            <label>Titre
              <input id="gen-title" type="text" value="${esc(g.title)}" />
            </label>
            ${g.kind === "custom" ? `
              <label>Mode d'affichage
                <select id="gen-mode">
                  <option value="moniteur_full" ${g.mode === "moniteur_full" ? "selected" : ""}>Moniteur Énergie Complet (DomoLink26 — LINKY + ÉLEC + GAZ)</option>
                  <option value="moniteur_linky_24h" ${g.mode === "moniteur_linky_24h" ? "selected" : ""}>Moniteur LINKY 24h (Jauge + Courbe de Charge)</option>
                  <option value="moniteur_elec" ${g.mode === "moniteur_elec" ? "selected" : ""}>Moniteur ÉLEC (Jour / Mois + EDF Tempo)</option>
                  <option value="moniteur_gaz" ${g.mode === "moniteur_gaz" ? "selected" : ""}>Moniteur GAZ (Jauge Gazpar + Jour / Mois)</option>
                  <option value="energy_full" ${g.mode === "energy_full" ? "selected" : ""}>Complet Classique (Élec + Gaz + Usages + Budget)</option>
                  <option value="compact" ${g.mode === "compact" ? "selected" : ""}>Compact (Élec + Gaz)</option>
                  <option value="billing" ${g.mode === "billing" ? "selected" : ""}>Facturation uniquement</option>
                </select>
              </label>
              ${String(g.mode || "").startsWith("moniteur_") ? `
                <label>Onglet initial
                  <select id="gen-default-tab">
                    <option value="linky" ${g.default_tab === "linky" ? "selected" : ""}>LINKY (24h)</option>
                    <option value="elec" ${g.default_tab === "elec" ? "selected" : ""}>ÉLEC</option>
                    <option value="gas" ${g.default_tab === "gas" ? "selected" : ""}>GAZ</option>
                  </select>
                </label>
                <label>Période par défaut (ÉLEC / GAZ)
                  <select id="gen-default-period">
                    <option value="daily" ${g.default_period === "daily" ? "selected" : ""}>Jour (14 derniers jours)</option>
                    <option value="monthly" ${g.default_period === "monthly" ? "selected" : ""}>Mois (Jan–Déc N vs N-1)</option>
                  </select>
                </label>
                <label>Préfixe ZLinky_TIC optionnel
                  <input id="gen-zlinky-prefix" type="text" value="${esc(g.zlinky_device_prefix || "")}" placeholder="Auto-détection si vide (ex: zlinky_tic)" />
                </label>
                <label>Capteur puissance VA optionnel
                  <input id="gen-power-sensor" type="text" value="${esc(g.power_sensor || "")}" placeholder="Auto-détection si vide" />
                </label>
                <label>Capteur énergie jour optionnel
                  <input id="gen-daily-sensor" type="text" value="${esc(g.daily_energy_sensor || "")}" placeholder="Auto-détection si vide" />
                </label>
              ` : `
                <label>Thème
                  <select id="gen-theme">
                    <option value="domolink_dark" ${g.theme === "domolink_dark" ? "selected" : ""}>DomoLink Dark</option>
                    <option value="ha_default" ${g.theme === "ha_default" ? "selected" : ""}>Home Assistant Natif</option>
                  </select>
                </label>
                <div class="chk-row">
                  <label class="chk"><input id="gen-cost" type="checkbox" ${g.show_cost ? "checked" : ""}/> Coûts (€)</label>
                  <label class="chk"><input id="gen-annual" type="checkbox" ${g.show_annual_comparison ? "checked" : ""}/> Comparatif N / N-1</label>
                  <label class="chk"><input id="gen-usages" type="checkbox" ${g.show_usages ? "checked" : ""}/> Usages (%)</label>
                  <label class="chk"><input id="gen-billing" type="checkbox" ${g.show_billing ? "checked" : ""}/> Facturation</label>
                </div>
              `}
            ` : ""}
          </div>
          <pre class="yaml-box" id="yaml-output">${esc(yaml)}</pre>
          <div class="actions" style="margin-top:12px">
            <button class="btn primary" id="copy-yaml">📋 Copier le YAML</button>
          </div>
        </div>

        <div class="card">
          <div class="card-top"><span class="kicker">👁️ Aperçu en Direct</span></div>
          <div id="card-preview-host" style="margin-top:8px"></div>
        </div>
      </div>
    `;
  }

  _renderSettings(acc) {
    const entities = Object.entries(acc.entities || {});
    const zl = acc.zlinky || {};
    const zEnabled = zl.enabled !== false;
    const zDevices = zl.detected_devices || [];
    const zSlots = zl.slots || [];
    const zLive = zl.live || {};
    const availableSensors = zl.available_sensors || [];

    const activePrefix = zl.configured_prefix || zl.device_prefix || "";

    return `
      <datalist id="zlinky-sensors-datalist">
        ${availableSensors
          .map(
            (s) =>
              `<option value="${esc(s.entity_id)}">${esc(s.friendly_name)} (${esc(s.state)}${s.unit ? " " + esc(s.unit) : ""})</option>`
          )
          .join("")}
      </datalist>

      <div class="card" style="margin-bottom:16px;border-color:rgba(16,185,129,0.35);">
        <div class="card-top" style="flex-wrap:wrap;gap:10px;">
          <div>
            <span class="kicker" style="color:#34d399;">⚡ Passerelle Temps Réel ZLinky_TIC (Zigbee2MQTT · ZHA · deCONZ / Dresden · TIC)</span>
            <p class="muted" style="margin:4px 0 0;">
              Associez automatiquement ou manuellement votre capteur Zigbee <b>ZLinky_TIC</b> pour alimenter le Moniteur Énergie en temps réel (Puissance VA, Tension V, Intensité A, PTEC HP/HC, Index live).
            </p>
          </div>
          <div class="actions">
            <span class="pill ${zl.detected ? "ok" : "neutral"}">
              ${zl.detected ? `${zl.matched_count || 0} capteur(s) ZLinky détecté(s) (${zl.active_count || 0} en ligne)` : "Aucun ZLinky détecté"}
            </span>
            <button class="btn" id="zlinky-autodetect-btn" title="Relancer l'auto-détection complète des entités ZLinky_TIC">🔍 Auto-détecter ZLinky_TIC</button>
          </div>
        </div>

        ${zl.detected ? `
          <div class="zlive-box">
            <div class="zlive-item"><span>Puissance (SINSTS)</span><b>${zLive.power_va !== null && zLive.power_va !== undefined ? `${fmt(zLive.power_va, 0)} VA` : "--"}</b></div>
            <div class="zlive-item"><span>Tension (URMS1)</span><b>${zLive.voltage_v !== null && zLive.voltage_v !== undefined ? `${fmt(zLive.voltage_v, 0)} V` : "--"}</b></div>
            <div class="zlive-item"><span>Intensité (IRMS1)</span><b>${zLive.current_a !== null && zLive.current_a !== undefined ? `${fmt(zLive.current_a, 2)} A` : "--"}</b></div>
            <div class="zlive-item"><span>Tarif (PTEC)</span><b>${esc(zLive.ptec || "--")}</b></div>
            <div class="zlive-item"><span>Index Total (EAST)</span><b>${zLive.index_total_kwh !== null && zLive.index_total_kwh !== undefined ? `${fmt(zLive.index_total_kwh, 2)} kWh` : "--"}</b></div>
            <div class="zlive-item"><span>Index HC / HP</span><b>${zLive.index_hc_kwh !== null && zLive.index_hc_kwh !== undefined ? fmt(zLive.index_hc_kwh, 1) : "--"} / ${zLive.index_hp_kwh !== null && zLive.index_hp_kwh !== undefined ? fmt(zLive.index_hp_kwh, 1) : "--"} kWh</b></div>
            <div class="zlive-item"><span>Pic Jour (SMAXSN)</span><b>${zLive.max_power_va !== null && zLive.max_power_va !== undefined ? `${fmt(zLive.max_power_va, 0)} VA` : "--"}</b></div>
            <div class="zlive-item"><span>Souscrite (PREF)</span><b>${zLive.pref_kva !== null && zLive.pref_kva !== undefined ? `${fmt(zLive.pref_kva, 0)} kVA` : "--"}</b></div>
          </div>
        ` : ""}

        <div class="divider"></div>

        <div class="zform-top">
          <label class="chk" style="font-size:13px;font-weight:700;color:#f8fafc;">
            <input type="checkbox" id="zl-enabled" ${zEnabled ? "checked" : ""} />
            Activer la fusion Temps Réel ZLinky_TIC dans le Moniteur Énergie
          </label>

          <div class="zprefix-row">
            ${zDevices.length ? `
              <label style="flex:1;min-width:240px;">Appareils ZLinky / TIC détectés dans Home Assistant
                <select id="zl-device-select">
                  <option value="">-- Sélectionner un appareil détecté --</option>
                  ${zDevices
                    .map(
                      (d) =>
                        `<option value="${esc(d.prefix)}" ${d.prefix === activePrefix ? "selected" : ""}>${esc(d.label)} [sensor.${esc(d.prefix)}_*]</option>`
                    )
                    .join("")}
                </select>
              </label>
            ` : ""}
            <label style="flex:1;min-width:240px;">Nom / Préfixe du capteur ZLinky_TIC (Z2M, ZHA, deCONZ)
              <input type="text" id="zl-prefix-input" value="${esc(activePrefix)}" placeholder="ex: zlinky_tic ou lixee_zlinky_tic" />
            </label>
          </div>
        </div>

        <div class="zslots-grid">
          ${zSlots
            .map((slot) => {
              const hasEnt = Boolean(slot.entity_id);
              const isOnline = hasEnt && slot.state && slot.state !== "unknown" && slot.state !== "unavailable";
              const stateBadge = hasEnt
                ? `<span class="pill ${isOnline ? "ok" : "warn"}">${slot.is_manual ? "Manuel" : "Auto"} · ${esc(slot.state || "--")}${slot.unit ? " " + esc(slot.unit) : ""}</span>`
                : `<span class="pill neutral">Non assigné</span>`;
              return `
                <div class="zslot-card">
                  <div class="zslot-head">
                    <span class="zslot-title">${esc(slot.label)}</span>
                    ${stateBadge}
                  </div>
                  <input
                    type="text"
                    class="zslot-input"
                    list="zlinky-sensors-datalist"
                    data-zslot-key="${esc(slot.key)}"
                    value="${esc(slot.entity_id || "")}"
                    placeholder="${esc(slot.hint || "sensor.zlinky_tic_...")}"
                  />
                  <div class="zslot-hint">${esc(slot.hint || "")}</div>
                </div>
              `;
            })
            .join("")}
        </div>

        <div class="actions" style="margin-top:16px;justify-content:space-between;">
          <span class="muted">Astuce : laissez un champ vide pour utiliser l'auto-détection, ou saisissez/sélectionnez n'importe quelle entité <code>sensor.*</code> de Home Assistant.</span>
          <div class="actions">
            <button class="btn" id="zlinky-clear-btn">♻️ Réinitialiser en Tout-Auto</button>
            <button class="btn primary" id="zlinky-save-btn">💾 Enregistrer la configuration ZLinky_TIC</button>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-top"><span class="kicker">⚙️ Contrat & Entités ENGIE Home Assistant (${entities.length})</span></div>
        <p class="muted" style="margin:6px 0 12px">Pour modifier les intervalles de polling ENGIE Cloud, vous pouvez aussi passer par <b>Paramètres → Appareils et services → DomoLink ENGIE → Configurer</b>.</p>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Clé interne</th><th>Entity ID Home Assistant</th></tr></thead>
            <tbody>
              ${entities.map(([k, eid]) => `<tr><td><code>${esc(k)}</code></td><td><code>${esc(eid)}</code></td></tr>`).join("") || `<tr><td colspan="2" class="muted">Aucune entité enregistrée</td></tr>`}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  _render() {
    if (!this.shadowRoot) return;
    const accs = this._snapshot?.accounts || [];
    const acc = this._getCurrentAccount();

    let bodyHtml = `<div class="card muted">Chargement des données ENGIE...</div>`;
    if (acc) {
      if (this._activeTab === "moniteur" && window.DomoLinkEngieMoniteur) {
        bodyHtml = `<div id="moniteur-live-host">${window.DomoLinkEngieMoniteur.renderMoniteurHTML(
          acc,
          this._hass,
          this._moniteurState,
          {},
          () => this._renderMoniteurInPlace()
        )}</div>`;
      } else if (this._activeTab === "overview") bodyHtml = this._renderOverview(acc);
      else if (this._activeTab === "history") bodyHtml = this._renderHistoryUsages(acc);
      else if (this._activeTab === "billing") bodyHtml = this._renderBilling(acc);
      else if (this._activeTab === "generator") bodyHtml = this._renderGenerator(acc);
      else if (this._activeTab === "settings") bodyHtml = this._renderSettings(acc);
    } else if (!this._loading) {
      bodyHtml = `<div class="card">Aucun contrat DomoLink ENGIE configuré pour le moment.</div>`;
    }

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; min-height: 100vh; background: #0b1120; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; }
        .wrap { max-width: 1280px; margin: 0 auto; padding: 20px; }
        .topbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }
        .brand { display: flex; align-items: center; gap: 12px; }
        .brand-icon { width: 42px; height: 42px; border-radius: 14px; background: linear-gradient(135deg, #0ea5e9, #2563eb); display: flex; align-items: center; justify-content: center; font-size: 20px; }
        .brand h1 { margin: 0; font-size: 20px; font-weight: 800; }
        .brand p { margin: 2px 0 0; font-size: 12px; color: #94a3b8; }
        .tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 18px; }
        .tab-btn { background: rgba(30,41,59,0.65); color: #cbd5e1; border: 1px solid rgba(148,163,184,0.16); border-radius: 12px; padding: 9px 14px; font-weight: 700; font-size: 13px; cursor: pointer; }
        .tab-btn.active { background: linear-gradient(135deg, #0284c7, #2563eb); color: #fff; border-color: transparent; }
        .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 16px; }
        .card { background: rgba(15,23,42,0.8); border: 1px solid rgba(148,163,184,0.16); border-radius: 20px; padding: 18px; backdrop-filter: blur(12px); }
        .card-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .kicker { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: #94a3b8; font-weight: 800; }
        .big { font-size: 34px; font-weight: 900; letter-spacing: -1px; margin: 4px 0 10px; }
        .big span { font-size: 14px; font-weight: 600; color: #94a3b8; }
        .subrow { display: flex; justify-content: space-between; gap: 10px; font-size: 13px; margin-top: 6px; flex-wrap: wrap; }
        .muted { color: #94a3b8; font-size: 12px; }
        .divider { height: 1px; background: rgba(148,163,184,0.14); margin: 12px 0; }
        .pill { font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 999px; }
        .pill.ok { background: rgba(16,185,129,0.18); color: #34d399; }
        .pill.warn { background: rgba(245,158,11,0.18); color: #fbbf24; }
        .pill.neutral { background: rgba(148,163,184,0.18); color: #cbd5e1; }
        .btn { background: rgba(30,41,59,0.9); color: #f8fafc; border: 1px solid rgba(148,163,184,0.25); border-radius: 10px; padding: 8px 14px; font-weight: 700; font-size: 13px; cursor: pointer; }
        .btn.primary { background: linear-gradient(135deg, #0ea5e9, #2563eb); border-color: transparent; }
        .actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
        .bar-row { margin-bottom: 10px; }
        .bar-label { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 4px; }
        .bar-track { height: 8px; border-radius: 999px; background: rgba(148,163,184,0.15); overflow: hidden; }
        .bar-fill { height: 100%; border-radius: 999px; }
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 12px; }
        th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid rgba(148,163,184,0.1); }
        th { color: #94a3b8; font-size: 11px; text-transform: uppercase; }
        .form-grid { display: flex; flex-direction: column; gap: 10px; }
        .form-grid label, .zprefix-row label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; font-weight: 700; color: #cbd5e1; }
        input[type="text"], select { background: #1e293b; color: #f8fafc; border: 1px solid rgba(148,163,184,0.25); border-radius: 8px; padding: 8px 10px; font-size: 13px; box-sizing: border-box; width: 100%; }
        .chk-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 4px; }
        .chk { display: inline-flex; flex-direction: row !important; align-items: center; gap: 8px; font-weight: 500 !important; cursor: pointer; }
        .yaml-box { background: #020617; border: 1px solid rgba(148,163,184,0.2); border-radius: 12px; padding: 12px; font-size: 12px; overflow-x: auto; color: #38bdf8; margin-top: 12px; }
        .zlive-box { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-top: 12px; padding: 12px; border-radius: 14px; background: rgba(16,185,129,0.07); border: 1px solid rgba(16,185,129,0.22); }
        .zlive-item { display: flex; flex-direction: column; gap: 2px; font-size: 11px; color: #94a3b8; }
        .zlive-item b { font-size: 14px; color: #f8fafc; }
        .zform-top { display: flex; flex-direction: column; gap: 12px; margin-bottom: 14px; }
        .zprefix-row { display: flex; flex-wrap: wrap; gap: 12px; }
        .zslots-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }
        .zslot-card { background: rgba(30,41,59,0.45); border: 1px solid rgba(148,163,184,0.14); border-radius: 12px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; }
        .zslot-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
        .zslot-title { font-size: 12px; font-weight: 700; color: #e2e8f0; }
        .zslot-hint { font-size: 11px; color: #64748b; }
        .toast { position: fixed; bottom: 20px; right: 20px; background: #0ea5e9; color: #fff; padding: 10px 16px; border-radius: 12px; font-weight: 700; font-size: 13px; box-shadow: 0 10px 25px rgba(0,0,0,0.4); z-index: 99; }
      </style>
      <div class="wrap">
        <div class="topbar">
          <div class="brand">
            <button class="btn" id="menu-btn" title="Menu Home Assistant">☰</button>
            <div class="brand-icon">⚡</div>
            <div>
              <h1>DomoLink ENGIE</h1>
              <p>Électricité Linky · ZLinky_TIC Live · Gaz Gazpar · Facturation · Suite DomoLink v${esc(this._snapshot?.version || "1.3.0")}</p>
            </div>
          </div>
          <div class="actions">
            ${accs.length > 1 ? `
              <select id="cc-select" style="width:auto;">
                ${accs.map((a) => `<option value="${esc(a.entry_id)}" ${a.entry_id === acc?.entry_id ? "selected" : ""}>Contrat ${esc(a.compte_contrat)} (${esc(a.ville)})</option>`).join("")}
              </select>
            ` : ""}
            <button class="btn primary" id="refresh-btn">${this._loading ? "⏳ Synchro..." : "🔄 Actualiser"}</button>
          </div>
        </div>

        <div class="tabs">
          <button class="tab-btn ${this._activeTab === "moniteur" ? "active" : ""}" data-tab="moniteur">⚡ Moniteur Énergie</button>
          <button class="tab-btn ${this._activeTab === "overview" ? "active" : ""}" data-tab="overview">📋 Vue d'ensemble ENGIE</button>
          <button class="tab-btn ${this._activeTab === "history" ? "active" : ""}" data-tab="history">📊 Historique & Usages</button>
          <button class="tab-btn ${this._activeTab === "billing" ? "active" : ""}" data-tab="billing">💶 Facturation & Échéancier</button>
          <button class="tab-btn ${this._activeTab === "generator" ? "active" : ""}" data-tab="generator">🎨 Générateur Lovelace</button>
          <button class="tab-btn ${this._activeTab === "settings" ? "active" : ""}" data-tab="settings">⚙️ Paramétrage & ZLinky_TIC</button>
        </div>

        ${bodyHtml}
      </div>
      ${this._toast ? `<div class="toast">${esc(this._toast)}</div>` : ""}
    `;

    this._bindEvents(acc);
  }

  _bindEvents(acc) {
    this.shadowRoot.getElementById("menu-btn")?.addEventListener("click", () => {
      this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }));
    });
    this.shadowRoot.getElementById("cc-select")?.addEventListener("change", (e) => {
      this._selectedEntryId = e.target.value;
      this._render();
    });
    this.shadowRoot.getElementById("refresh-btn")?.addEventListener("click", async () => {
      if (!this._hass) return;
      this._loading = true;
      this._render();
      try {
        await this._hass.callWS({ type: "domolink_engie/refresh_now", entry_id: acc?.entry_id });
        await this._loadSnapshot();
        this._showToast("Données ENGIE & ZLinky_TIC actualisées.");
      } catch (err) {
        this._showToast(`Erreur refresh: ${err.message || err}`);
      }
    });

    this.shadowRoot.querySelectorAll("[data-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._activeTab = btn.getAttribute("data-tab");
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("[data-dlm-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._moniteurState.tab = btn.getAttribute("data-dlm-tab");
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("[data-dlm-period]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._moniteurState.period = btn.getAttribute("data-dlm-period");
        this._render();
      });
    });

    this.shadowRoot.querySelectorAll("[data-import]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const months = Number(btn.getAttribute("data-import") || 12);
        try {
          const res = await this._hass.callWS({
            type: "domolink_engie/import_history",
            entry_id: acc?.entry_id,
            months,
          });
          this._showToast(`Import Recorder terminé (${res.imported_points || 0} points sur ${months} mois).`);
        } catch (err) {
          this._showToast(`Erreur import: ${err.message || err}`);
        }
      });
    });

    // Settings Tab: ZLinky_TIC Auto-detection & Manual configuration events
    if (this._activeTab === "settings") {
      const devSelect = this.shadowRoot.getElementById("zl-device-select");
      const prefInput = this.shadowRoot.getElementById("zl-prefix-input");

      devSelect?.addEventListener("change", async (e) => {
        const chosenPrefix = e.target.value;
        if (prefInput) prefInput.value = chosenPrefix;
        if (!this._hass) return;
        try {
          const res = await this._hass.callWS({
            type: "domolink_engie/save_zlinky_config",
            entry_id: acc?.entry_id,
            reset_auto: false,
            zlinky_enabled: true,
            zlinky_device_prefix: chosenPrefix,
          });
          this._snapshot = res;
          this._showToast(`Appareil ZLinky_TIC '${chosenPrefix || "Auto"}' sélectionné.`);
        } catch (err) {
          this._showToast(`Erreur sélection ZLinky: ${err.message || err}`);
        }
      });

      const triggerAutoReset = async () => {
        if (!this._hass) return;
        try {
          const res = await this._hass.callWS({
            type: "domolink_engie/save_zlinky_config",
            entry_id: acc?.entry_id,
            reset_auto: true,
            zlinky_enabled: true,
          });
          this._snapshot = res;
          const updatedAcc = this._getCurrentAccount();
          const count = updatedAcc?.zlinky?.matched_count || 0;
          this._showToast(`Auto-détection ZLinky_TIC terminée : ${count} capteur(s) associé(s).`);
        } catch (err) {
          this._showToast(`Erreur auto-détection: ${err.message || err}`);
        }
      };

      this.shadowRoot.getElementById("zlinky-autodetect-btn")?.addEventListener("click", triggerAutoReset);
      this.shadowRoot.getElementById("zlinky-clear-btn")?.addEventListener("click", triggerAutoReset);

      this.shadowRoot.getElementById("zlinky-save-btn")?.addEventListener("click", async () => {
        if (!this._hass) return;
        const enabledEl = this.shadowRoot.getElementById("zl-enabled");
        const payload = {
          type: "domolink_engie/save_zlinky_config",
          entry_id: acc?.entry_id,
          reset_auto: false,
          zlinky_enabled: enabledEl ? Boolean(enabledEl.checked) : true,
          zlinky_device_prefix: prefInput ? prefInput.value.trim() : "",
        };
        this.shadowRoot.querySelectorAll(".zslot-input").forEach((inp) => {
          const k = inp.getAttribute("data-zslot-key");
          if (k) payload[k] = inp.value.trim();
        });
        try {
          const res = await this._hass.callWS(payload);
          this._snapshot = res;
          this._showToast("Configuration ZLinky_TIC enregistrée avec succès !");
        } catch (err) {
          this._showToast(`Erreur sauvegarde ZLinky_TIC: ${err.message || err}`);
        }
      });
    }

    if (this._activeTab === "generator") {
      const bindGen = (id, key, isBool = false) => {
        const el = this.shadowRoot.getElementById(id);
        if (!el) return;
        el.addEventListener("change", () => {
          this._cardGen[key] = isBool ? el.checked : el.value;
          this._render();
        });
      };
      bindGen("gen-kind", "kind");
      bindGen("gen-title", "title");
      bindGen("gen-mode", "mode");
      bindGen("gen-default-tab", "default_tab");
      bindGen("gen-default-period", "default_period");
      bindGen("gen-zlinky-prefix", "zlinky_device_prefix");
      bindGen("gen-power-sensor", "power_sensor");
      bindGen("gen-daily-sensor", "daily_energy_sensor");
      bindGen("gen-theme", "theme");
      bindGen("gen-cost", "show_cost", true);
      bindGen("gen-annual", "show_annual_comparison", true);
      bindGen("gen-usages", "show_usages", true);
      bindGen("gen-billing", "show_billing", true);

      this.shadowRoot.getElementById("copy-yaml")?.addEventListener("click", async () => {
        const yaml = this._buildLovelaceYaml(acc);
        try {
          await navigator.clipboard.writeText(yaml);
          this._showToast("YAML Lovelace copié dans le presse-papier !");
        } catch {
          this._showToast("Sélectionnez et copiez le bloc YAML ci-dessus.");
        }
      });

      const host = this.shadowRoot.getElementById("card-preview-host");
      if (host) {
        if (this._cardGen.kind === "custom") {
          const cardEl = document.createElement("domolink-engie-card");
          cardEl.setConfig({
            title: this._cardGen.title,
            compte_contrat: acc?.compte_contrat || "",
            mode: this._cardGen.mode,
            default_tab: this._cardGen.default_tab,
            default_period: this._cardGen.default_period,
            zlinky_device_prefix: this._cardGen.zlinky_device_prefix,
            power_sensor: this._cardGen.power_sensor,
            daily_energy_sensor: this._cardGen.daily_energy_sensor,
            theme: this._cardGen.theme,
            show_cost: this._cardGen.show_cost,
            show_annual_comparison: this._cardGen.show_annual_comparison,
            show_usages: this._cardGen.show_usages,
            show_billing: this._cardGen.show_billing,
          });
          cardEl._snapshotAcc = acc;
          cardEl.hass = this._hass;
          host.appendChild(cardEl);
        } else {
          host.innerHTML = `<div class="muted">Aperçu du pack YAML natif prêt à coller dans un dashboard Home Assistant (mode Éditeur de code).</div>`;
        }
      }
    }
  }
}

if (!customElements.get(PANEL_TAG)) {
  customElements.define(PANEL_TAG, DomoLinkEngiePanel);
}
