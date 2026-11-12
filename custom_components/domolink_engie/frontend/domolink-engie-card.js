/**
 * DomoLink ENGIE — Custom Lovelace Card & Visual Editor
 * Element: <domolink-engie-card> / type: custom:domolink-engie-card
 */

const CARD_TAG = "domolink-engie-card";
const EDITOR_TAG = "domolink-engie-card-editor";

function escHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtNum(val, digits = 2) {
  const n = Number(val);
  if (!Number.isFinite(n)) return "--";
  return n.toLocaleString("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function discoverContracts(hass) {
  if (!hass || !hass.states) return [];
  const found = new Set();
  for (const eid of Object.keys(hass.states)) {
    const m = eid.match(/^sensor\.engie_(\d+)_/);
    if (m) found.add(m[1]);
  }
  return Array.from(found).sort();
}

function findEngieEntity(hass, cc, suffix) {
  if (!hass || !hass.states) return null;
  if (cc) {
    const exact = `sensor.engie_${cc}_${suffix}`;
    if (hass.states[exact]) return hass.states[exact];
  }
  const needle = `_${suffix}`;
  for (const [eid, st] of Object.entries(hass.states)) {
    if (eid.startsWith("sensor.engie_") && eid.endsWith(needle)) {
      return st;
    }
  }
  return null;
}

class DomoLinkEngieCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  static getStubConfig(hass) {
    const contracts = discoverContracts(hass);
    return {
      type: `custom:${CARD_TAG}`,
      title: "ENGIE Électricité & Gaz",
      compte_contrat: contracts[0] || "",
      mode: "energy_full",
      show_cost: true,
      show_annual_comparison: true,
      show_usages: true,
      show_billing: true,
      theme: "domolink_dark",
    };
  }

  setConfig(config) {
    this._config = {
      title: "ENGIE Électricité & Gaz",
      compte_contrat: "",
      mode: "energy_full",
      show_cost: true,
      show_annual_comparison: true,
      show_usages: true,
      show_billing: true,
      theme: "domolink_dark",
      ...config,
    };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    const mode = this._config.mode || "energy_full";
    if (mode === "compact") return 3;
    if (mode === "billing") return 4;
    return 6;
  }

  _render() {
    if (!this.shadowRoot) return;
    const cfg = this._config;
    const hass = this._hass;
    const cc = cfg.compte_contrat || discoverContracts(hass)[0] || "";
    const isDark = cfg.theme !== "ha_default";

    const st = (suffix) => findEngieEntity(hass, cc, suffix);
    const val = (suffix) => st(suffix)?.state;

    const elecMois = val("consommation_electricite_mois");
    const elecCost = val("cout_electricite_mois");
    const elecAn = val("consommation_electricite_annuelle");
    const elecAnAttrs = st("consommation_electricite_annuelle")?.attributes || {};
    const elecVar = val("variation_consommation_electricite");
    const elecRepart = st("repartition_usages_electricite")?.attributes?.items || [];

    const gazIdx = val("index_compteur_gaz");
    const gazMois = val("consommation_gaz_mois_kwh");
    const gazCost = val("cout_gaz_mois");
    const gazAn = val("consommation_gaz_annuelle_kwh");
    const gazAnAttrs = st("consommation_gaz_annuelle_kwh")?.attributes || {};
    const gazVar = val("variation_consommation_gaz");
    const gazRepart = st("repartition_usages_gaz")?.attributes?.items || [];

    const solde = val("solde_a_payer");
    const mens = val("mensualite_globale");
    const mensAttrs = st("mensualite_globale")?.attributes || {};
    const lastPay = val("dernier_prelevement");
    const lastPayAttrs = st("dernier_prelevement")?.attributes || {};
    const cagnotte = val("cagnotte_engagement_engie");

    const mode = cfg.mode || "energy_full";
    const showElec = ["energy_full", "compact", "elec_only"].includes(mode);
    const showGaz = ["energy_full", "compact", "gaz_only"].includes(mode);
    const showBilling =
      mode === "billing" || (mode === "energy_full" && cfg.show_billing);

    const varBadge = (v) => {
      const n = Number(v);
      if (!Number.isFinite(n)) return "";
      const cls = n > 0 ? "badge-up" : n < 0 ? "badge-down" : "badge-flat";
      const sign = n > 0 ? "+" : "";
      return `<span class="badge ${cls}">${sign}${fmtNum(n, 0)}%</span>`;
    };

    const renderUsageBar = (items, title) => {
      if (!cfg.show_usages || !items || !items.length) return "";
      const segs = items
        .map(
          (it) =>
            `<div class="seg" style="width:${Number(it.valeur) || 0}%;background:#${escHtml(it.couleur || "38bdf8")}" title="${escHtml(it.libelle)}: ${fmtNum(it.valeur, 0)}%"></div>`
        )
        .join("");
      const leg = items
        .slice(0, 4)
        .map(
          (it) =>
            `<span><i style="background:#${escHtml(it.couleur || "38bdf8")}"></i>${escHtml(it.libelle)} ${fmtNum(it.valeur, 0)}%</span>`
        )
        .join("");
      return `
        <div class="usage-block">
          <div class="usage-title">${escHtml(title)}</div>
          <div class="usage-bar">${segs}</div>
          <div class="usage-legend">${leg}</div>
        </div>
      `;
    };

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card {
          background: ${isDark ? "linear-gradient(145deg, #0f172a 0%, #111827 100%)" : "var(--ha-card-background, var(--card-background-color))"};
          color: ${isDark ? "#f8fafc" : "var(--primary-text-color)"};
          border: 1px solid ${isDark ? "rgba(148,163,184,0.16)" : "var(--divider-color, rgba(0,0,0,0.12))"};
          border-radius: 20px;
          padding: 16px;
          font-family: var(--paper-font-body1_-_font-family, system-ui, sans-serif);
        }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
        .title-wrap { display: flex; align-items: center; gap: 10px; }
        .logo {
          width: 34px; height: 34px; border-radius: 10px;
          background: linear-gradient(135deg, #0ea5e9, #2563eb);
          display: flex; align-items: center; justify-content: center; font-size: 18px; color: #fff;
        }
        .title { font-weight: 800; font-size: 15px; letter-spacing: -0.2px; }
        .sub { font-size: 11px; color: ${isDark ? "#94a3b8" : "var(--secondary-text-color)"}; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(195px, 1fr)); gap: 12px; }
        .tile {
          background: ${isDark ? "rgba(30,41,59,0.55)" : "rgba(128,128,128,0.06)"};
          border: 1px solid ${isDark ? "rgba(148,163,184,0.12)" : "rgba(128,128,128,0.15)"};
          border-radius: 16px; padding: 14px;
        }
        .tile-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
        .kicker { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 700; color: ${isDark ? "#94a3b8" : "var(--secondary-text-color)"}; }
        .val { font-size: 24px; font-weight: 800; letter-spacing: -0.5px; }
        .unit { font-size: 12px; font-weight: 600; color: ${isDark ? "#94a3b8" : "var(--secondary-text-color)"}; margin-left: 3px; }
        .meta { margin-top: 6px; font-size: 12px; color: ${isDark ? "#cbd5e1" : "var(--secondary-text-color)"}; display: flex; justify-content: space-between; gap: 6px; flex-wrap: wrap; }
        .badge { font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 999px; }
        .badge-up { background: rgba(245,158,11,0.18); color: #fbbf24; }
        .badge-down { background: rgba(16,185,129,0.18); color: #34d399; }
        .badge-flat { background: rgba(148,163,184,0.18); color: #cbd5e1; }
        .usage-block { margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(148,163,184,0.12); }
        .usage-title { font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: #94a3b8; margin-bottom: 6px; font-weight: 700; }
        .usage-bar { display: flex; height: 8px; border-radius: 999px; overflow: hidden; background: rgba(148,163,184,0.15); }
        .seg { height: 100%; }
        .usage-legend { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; font-size: 11px; color: ${isDark ? "#cbd5e1" : "var(--secondary-text-color)"}; }
        .usage-legend i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 4px; }
        .billing-strip {
          margin-top: 12px; display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 8px;
          background: ${isDark ? "rgba(15,23,42,0.55)" : "rgba(128,128,128,0.05)"};
          border: 1px solid rgba(148,163,184,0.12); border-radius: 14px; padding: 10px 12px;
        }
        .b-item .b-lbl { font-size: 10px; text-transform: uppercase; color: #94a3b8; font-weight: 700; }
        .b-item .b-val { font-size: 14px; font-weight: 800; margin-top: 2px; }
        .b-item .b-sub { font-size: 10px; color: #94a3b8; }
      </style>
      <ha-card>
        <div class="header">
          <div class="title-wrap">
            <div class="logo">⚡</div>
            <div>
              <div class="title">${escHtml(cfg.title || "ENGIE Électricité & Gaz")}</div>
              <div class="sub">${cc ? `Contrat ${escHtml(cc)}` : "Auto-détection ENGIE"}</div>
            </div>
          </div>
          ${cagnotte !== undefined ? `<span class="badge badge-down">🎁 ${fmtNum(cagnotte, 0)} € cagnotte</span>` : ""}
        </div>

        <div class="grid">
          ${showElec ? `
            <div class="tile">
              <div class="tile-head">
                <span class="kicker">⚡ Électricité (Mois)</span>
                ${varBadge(elecVar)}
              </div>
              <div>
                <span class="val" style="color:#38bdf8">${fmtNum(elecMois, 1)}</span>
                <span class="unit">kWh</span>
              </div>
              <div class="meta">
                ${cfg.show_cost ? `<span>Coût : <b>${fmtNum(elecCost, 2)} €</b></span>` : ""}
                ${cfg.show_annual_comparison && elecAn !== undefined ? `<span>An : <b>${fmtNum(elecAn, 0)} kWh</b></span>` : ""}
              </div>
              ${cfg.show_annual_comparison && elecAnAttrs.conso_annee_precedente ? `<div class="meta"><span>N-1 : ${fmtNum(elecAnAttrs.conso_annee_precedente, 0)} kWh (${elecAnAttrs.variation_pct > 0 ? "+" : ""}${fmtNum(elecAnAttrs.variation_pct, 0)}%)</span></div>` : ""}
              ${mode === "energy_full" || mode === "elec_only" ? renderUsageBar(elecRepart, "Usages Électricité") : ""}
            </div>
          ` : ""}

          ${showGaz ? `
            <div class="tile">
              <div class="tile-head">
                <span class="kicker">🔥 Gaz Naturel (Mois)</span>
                ${varBadge(gazVar)}
              </div>
              <div>
                <span class="val" style="color:#fb923c">${fmtNum(gazMois, 0)}</span>
                <span class="unit">kWh</span>
              </div>
              <div class="meta">
                ${cfg.show_cost ? `<span>Coût : <b>${fmtNum(gazCost, 2)} €</b></span>` : ""}
                ${gazIdx !== undefined ? `<span>Index : <b>${fmtNum(gazIdx, 0)} m³</b></span>` : ""}
              </div>
              ${cfg.show_annual_comparison && gazAn !== undefined ? `<div class="meta"><span>An : <b>${fmtNum(gazAn, 0)} kWh</b></span>${gazAnAttrs.conso_annee_precedente ? `<span>N-1 : ${fmtNum(gazAnAttrs.conso_annee_precedente, 0)} kWh</span>` : ""}</div>` : ""}
              ${mode === "energy_full" || mode === "gaz_only" ? renderUsageBar(gazRepart, "Usages Gaz") : ""}
            </div>
          ` : ""}
        </div>

        ${showBilling ? `
          <div class="billing-strip">
            <div class="b-item">
              <div class="b-lbl">Mensualité</div>
              <div class="b-val">${fmtNum(mens, 2)} €</div>
              ${mensAttrs.mensualite_elec !== undefined ? `<div class="b-sub">⚡${fmtNum(mensAttrs.mensualite_elec, 0)}€ · 🔥${fmtNum(mensAttrs.mensualite_gaz, 0)}€</div>` : ""}
            </div>
            <div class="b-item">
              <div class="b-lbl">Solde</div>
              <div class="b-val">${fmtNum(solde, 2)} €</div>
              <div class="b-sub">${mensAttrs.prochaine_echeance ? `Éch. ${escHtml(mensAttrs.prochaine_echeance)}` : ""}</div>
            </div>
            <div class="b-item">
              <div class="b-lbl">Dernier Prélèvement</div>
              <div class="b-val">${fmtNum(lastPay, 2)} €</div>
              <div class="b-sub">${escHtml(lastPayAttrs.date || "")}</div>
            </div>
          </div>
        ` : ""}
      </ha-card>
    `;
  }
}

class DomoLinkEngieCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _emit(patch) {
    const newConfig = { ...this._config, ...patch };
    this._config = newConfig;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: newConfig },
        bubbles: true,
        composed: true,
      })
    );
  }

  _render() {
    if (!this.shadowRoot) return;
    const cfg = this._config;
    const contracts = discoverContracts(this._hass);

    this.shadowRoot.innerHTML = `
      <style>
        .form { display: flex; flex-direction: column; gap: 12px; padding: 8px 0; }
        label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; font-weight: 600; }
        input[type="text"], select {
          padding: 8px 10px; border-radius: 8px;
          border: 1px solid var(--divider-color, #444);
          background: var(--card-background-color, #1e293b);
          color: var(--primary-text-color, #fff);
        }
        .checks { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
        .chk { flex-direction: row; align-items: center; gap: 8px; font-weight: 500; }
      </style>
      <div class="form">
        <label>Titre de la carte
          <input type="text" id="title" value="${escHtml(cfg.title || "ENGIE Électricité & Gaz")}" />
        </label>
        <label>Compte Contrat (laisser vide = auto)
          <input type="text" id="cc" list="engie-ccs" value="${escHtml(cfg.compte_contrat || "")}" placeholder="${escHtml(contracts[0] || "Auto")}" />
          <datalist id="engie-ccs">${contracts.map((c) => `<option value="${escHtml(c)}">`).join("")}</datalist>
        </label>
        <label>Mode d'affichage
          <select id="mode">
            <option value="energy_full" ${cfg.mode === "energy_full" ? "selected" : ""}>Complet (Élec + Gaz + Usages + Budget)</option>
            <option value="compact" ${cfg.mode === "compact" ? "selected" : ""}>Compact (Élec + Gaz)</option>
            <option value="billing" ${cfg.mode === "billing" ? "selected" : ""}>Facturation & Mensualités</option>
            <option value="elec_only" ${cfg.mode === "elec_only" ? "selected" : ""}>Électricité uniquement</option>
            <option value="gaz_only" ${cfg.mode === "gaz_only" ? "selected" : ""}>Gaz uniquement</option>
          </select>
        </label>
        <label>Thème
          <select id="theme">
            <option value="domolink_dark" ${cfg.theme !== "ha_default" ? "selected" : ""}>DomoLink Dark</option>
            <option value="ha_default" ${cfg.theme === "ha_default" ? "selected" : ""}>Home Assistant Natif</option>
          </select>
        </label>
        <div class="checks">
          <label class="chk"><input type="checkbox" id="show_cost" ${cfg.show_cost !== false ? "checked" : ""}/> Afficher coûts (€)</label>
          <label class="chk"><input type="checkbox" id="show_annual" ${cfg.show_annual_comparison !== false ? "checked" : ""}/> Comparatif N / N-1</label>
          <label class="chk"><input type="checkbox" id="show_usages" ${cfg.show_usages !== false ? "checked" : ""}/> Répartition usages</label>
          <label class="chk"><input type="checkbox" id="show_billing" ${cfg.show_billing !== false ? "checked" : ""}/> Bandeau facturation</label>
        </div>
      </div>
    `;

    const bind = (id, key, isBool = false) => {
      const el = this.shadowRoot.getElementById(id);
      if (!el) return;
      el.addEventListener("change", () => this._emit({ [key]: isBool ? el.checked : el.value }));
    };
    bind("title", "title");
    bind("cc", "compte_contrat");
    bind("mode", "mode");
    bind("theme", "theme");
    bind("show_cost", "show_cost", true);
    bind("show_annual", "show_annual_comparison", true);
    bind("show_usages", "show_usages", true);
    bind("show_billing", "show_billing", true);
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, DomoLinkEngieCard);
}
if (!customElements.get(EDITOR_TAG)) {
  customElements.define(EDITOR_TAG, DomoLinkEngieCardEditor);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "DomoLink ENGIE Électricité & Gaz",
    description: "Suivi consommation Élec/Gaz, index Gazpar, usages et facturation ENGIE.",
    preview: true,
  });
}
