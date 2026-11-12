/**
 * DomoLink ENGIE — Custom Lovelace Card & Visual Editor + Moniteur Énergie (DomoLink26.IO Engine + ZLinky_TIC Live)
 * Version: 1.3.0
 */

(function () {
  const MONTHS_FR = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Août", "Sep", "Oct", "Nov", "Déc"];
  const ELEC_SEASONAL = [0.125, 0.115, 0.100, 0.080, 0.062, 0.052, 0.055, 0.048, 0.065, 0.088, 0.100, 0.110];
  const GAZ_SEASONAL = [0.175, 0.155, 0.125, 0.085, 0.040, 0.018, 0.015, 0.015, 0.042, 0.090, 0.120, 0.120];
  const HOURLY_PROFILE = [
    0.028, 0.025, 0.024, 0.023, 0.024, 0.027,
    0.042, 0.058, 0.055, 0.044, 0.040, 0.042,
    0.054, 0.048, 0.038, 0.035, 0.040, 0.052,
    0.068, 0.075, 0.065, 0.048, 0.038, 0.032,
  ];

  function esc(v) {
    return String(v ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function num(v, fallback = 0) {
    if (v === null || v === undefined || v === "" || v === "unknown" || v === "unavailable") {
      return fallback;
    }
    const n = Number(String(v).replace(",", "."));
    return Number.isFinite(n) ? n : fallback;
  }

  function fmt(v, digits = 1) {
    const n = num(v, NaN);
    if (!Number.isFinite(n)) return "—";
    return n.toLocaleString("fr-FR", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function fmtInt(v) {
    const n = num(v, NaN);
    if (!Number.isFinite(n)) return "—";
    return Math.round(n).toLocaleString("fr-FR");
  }

  function isHcHour(hour) {
    return hour >= 22 || hour < 6;
  }

  function isHcFromPtec(ptec) {
    if (!ptec) return null;
    const up = String(ptec).toUpperCase();
    if (up.includes("HC") || up.includes("CREUSE") || up.includes("TIER 1") || up.includes("TIER_1")) return true;
    if (up.includes("HP") || up.includes("PLEINE") || up.includes("BASE") || up.includes("NORMALE") || up.includes("TIER 2")) return false;
    return null;
  }

  const ZLINKY_TOKEN_RE = /(^|_)(zlinky|lixee|linky_tic|zlinky_tic|teleinfo|linky|tic)($|_)/i;

  function hasZlinkyToken(str) {
    return ZLINKY_TOKEN_RE.test(String(str || ""));
  }

  // Live 24h Recorder history cache so Courbe de Charge (24h) and Conso Aujourd'hui reflect real HA live telemetry
  const _liveHistoryCache = {
    key: "",
    ts: 0,
    loading: false,
    hourlyKwh: null,
    todayFromPowerKwh: null,
    todayHcFromPowerKwh: null,
    todayDeltaTotalKwh: null,
    todayDeltaHcKwh: null,
    listeners: new Set(),
  };

  function _parseHistPoint(pt) {
    if (!pt) return null;
    const rawS = pt.s !== undefined ? pt.s : pt.state;
    if (rawS === undefined || rawS === null || rawS === "unknown" || rawS === "unavailable" || rawS === "") return null;
    const val = Number(String(rawS).replace(",", "."));
    if (!Number.isFinite(val)) return null;
    let tsMs = NaN;
    if (typeof pt.lu === "number") tsMs = pt.lu * 1000;
    else if (typeof pt.lc === "number") tsMs = pt.lc * 1000;
    else if (pt.last_updated || pt.last_changed) tsMs = Date.parse(pt.last_updated || pt.last_changed);
    if (!Number.isFinite(tsMs)) return null;
    return { val, tsMs };
  }

  function ensureLive24hHistory(hass, companions, onReady) {
    if (typeof onReady === "function") {
      _liveHistoryCache.listeners.add(onReady);
    }
    if (!hass || typeof hass.callWS !== "function" || !companions) return _liveHistoryCache;

    const eids = [
      companions.powerSensor,
      companions.dailyEnergySensor,
      companions.indexTotalSensor,
      companions.indexHcSensor,
      companions.indexHpSensor,
    ].filter(Boolean);

    if (!eids.length) return _liveHistoryCache;

    const now = new Date();
    const key = `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}|${eids.join(",")}`;
    if (_liveHistoryCache.loading) return _liveHistoryCache;
    if (_liveHistoryCache.key === key && Date.now() - _liveHistoryCache.ts < 60000 && _liveHistoryCache.hourlyKwh) {
      return _liveHistoryCache;
    }

    _liveHistoryCache.loading = true;
    const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
    const startMs = startOfDay.getTime();
    const nowMs = now.getTime();
    const currentHour = now.getHours();
    const states = hass.states || {};

    hass
      .callWS({
        type: "history/history_during_period",
        start_time: startOfDay.toISOString(),
        end_time: now.toISOString(),
        entity_ids: eids,
        minimal_response: true,
        no_attributes: true,
        significant_changes_only: false,
      })
      .then((res) => {
        const histMap = res || {};

        // 1. Integrate powerSensor history into 24 hourly buckets (0..23)
        let hourlyFromPower = null;
        let todayFromPower = 0;
        let todayHcFromPower = 0;
        const pEid = companions.powerSensor;
        if (pEid && Array.isArray(histMap[pEid]) && histMap[pEid].length > 0) {
          const pts = histMap[pEid].map(_parseHistPoint).filter(Boolean).sort((a, b) => a.tsMs - b.tsMs);
          if (pts.length > 0) {
            const pUnit = String((states[pEid] && states[pEid].attributes && states[pEid].attributes.unit_of_measurement) || "VA").toLowerCase();
            const scale = pUnit === "kw" || pUnit === "kva" ? 1 : 0.001;
            hourlyFromPower = new Array(24).fill(0);

            for (let h = 0; h <= currentHour; h++) {
              const hStart = startMs + h * 3600000;
              const hEnd = Math.min(nowMs, hStart + 3600000);
              if (hEnd <= hStart) continue;

              // Find state active at hStart
              let lastVal = pts[0].val;
              for (const p of pts) {
                if (p.tsMs <= hStart) lastVal = p.val;
                else break;
              }

              let cursor = hStart;
              let energyKwh = 0;
              for (const p of pts) {
                if (p.tsMs <= hStart) continue;
                if (p.tsMs >= hEnd) break;
                const dtHours = (p.tsMs - cursor) / 3600000;
                if (dtHours > 0 && lastVal >= 0) {
                  energyKwh += lastVal * scale * dtHours;
                }
                cursor = p.tsMs;
                lastVal = p.val;
              }
              if (hEnd > cursor && lastVal >= 0) {
                energyKwh += lastVal * scale * ((hEnd - cursor) / 3600000);
              }

              todayFromPower += energyKwh;
              if (isHcHour(h)) todayHcFromPower += energyKwh;

              // For the current in-progress hour, display the average hourly power rate (kW/kVA) so the bar matches live consumption
              const elapsedFraction = (hEnd - hStart) / 3600000;
              const barKwh = h === currentHour && elapsedFraction > 0.02 && elapsedFraction < 0.9
                ? energyKwh / elapsedFraction
                : energyKwh;
              hourlyFromPower[h] = Number(Math.max(0, barKwh).toFixed(2));
            }
          }
        }

        // 2. Compute midnight-to-now deltas from indexTotalSensor / indexHcSensor / indexHpSensor / dailyEnergySensor
        const computeDeltaAndHourly = (eid) => {
          if (!eid || !Array.isArray(histMap[eid]) || histMap[eid].length < 2) return null;
          const pts = histMap[eid].map(_parseHistPoint).filter((p) => p && p.val >= 0).sort((a, b) => a.tsMs - b.tsMs);
          if (pts.length < 2) return null;
          const u = String((states[eid] && states[eid].attributes && states[eid].attributes.unit_of_measurement) || "kWh").toLowerCase();
          const scale = u === "wh" ? 0.001 : 1;
          const firstVal = pts[0].val * scale;
          const lastVal = pts[pts.length - 1].val * scale;
          const totalDelta = lastVal >= firstVal ? lastVal - firstVal : lastVal;

          const buckets = new Array(24).fill(0);
          for (let h = 0; h <= currentHour; h++) {
            const hStart = startMs + h * 3600000;
            const hEnd = hStart + 3600000;
            const inHour = pts.filter((p) => p.tsMs >= hStart && p.tsMs <= hEnd);
            if (inHour.length >= 2) {
              const d = (inHour[inHour.length - 1].val - inHour[0].val) * scale;
              if (d >= 0 && d < 25) buckets[h] = Number(d.toFixed(2));
            }
          }
          return { totalDelta: Number(totalDelta.toFixed(2)), buckets };
        };

        const totInfo = computeDeltaAndHourly(companions.indexTotalSensor) || computeDeltaAndHourly(companions.dailyEnergySensor);
        const hcInfo = computeDeltaAndHourly(companions.indexHcSensor);
        const hpInfo = computeDeltaAndHourly(companions.indexHpSensor);

        let deltaTotal = totInfo ? totInfo.totalDelta : null;
        if (deltaTotal === null && hcInfo && hpInfo) {
          deltaTotal = Number((hcInfo.totalDelta + hpInfo.totalDelta).toFixed(2));
        }

        const bestHourly =
          hourlyFromPower && hourlyFromPower.some((v) => v > 0)
            ? hourlyFromPower
            : totInfo && totInfo.buckets.some((v) => v > 0)
            ? totInfo.buckets
            : null;

        _liveHistoryCache.key = key;
        _liveHistoryCache.ts = Date.now();
        _liveHistoryCache.hourlyKwh = bestHourly;
        _liveHistoryCache.todayFromPowerKwh = todayFromPower > 0 ? Number(todayFromPower.toFixed(2)) : null;
        _liveHistoryCache.todayHcFromPowerKwh = todayFromPower > 0 ? Number(todayHcFromPower.toFixed(2)) : null;
        _liveHistoryCache.todayDeltaTotalKwh = deltaTotal !== null && deltaTotal > 0 && deltaTotal <= 150 ? deltaTotal : null;
        _liveHistoryCache.todayDeltaHcKwh = hcInfo && hcInfo.totalDelta >= 0 && hcInfo.totalDelta <= 120 ? hcInfo.totalDelta : null;
      })
      .catch(() => {})
      .finally(() => {
        _liveHistoryCache.loading = false;
        const cbs = Array.from(_liveHistoryCache.listeners);
        _liveHistoryCache.listeners.clear();
        cbs.forEach((cb) => {
          try {
            cb();
          } catch (_) {}
        });
      });

    return _liveHistoryCache;
  }

  /**
   * Auto-detect & resolve ZLinky_TIC (Z2M, ZHA, deCONZ/Dresden, TIC) + companion sensors in real time from hass.states
   */
  function autoDetectCompanionSensors(hass, config = {}, account = {}) {
    const states = (hass && hass.states) || {};
    const keys = Object.keys(states);
    const zlinkySnap = account.zlinky || {};
    const zlinkyResolved = zlinkySnap.resolved || {};
    const zlinkyConfigured = zlinkySnap.configured || {};
    const zlinkyEnabled = zlinkySnap.enabled !== false && config.zlinky_enabled !== false;
    const preferredPrefix = String(
      config.zlinky_device_prefix || zlinkySnap.configured_prefix || zlinkySnap.device_prefix || ""
    ).toLowerCase();

    const findBest = (manualCandidates, backendAutoCandidate, suffixes, validUnits = null, opts = {}) => {
      // 1. User manual overrides always win immediately
      for (const exp of manualCandidates) {
        if (exp && states[exp]) return exp;
      }
      if (!zlinkyEnabled && !opts.alwaysAllow) return null;

      let bestEid = null;
      let bestScore = -1;

      for (const eid of keys) {
        if (!eid.startsWith("sensor.") || eid.startsWith("sensor.engie_")) continue;
        const oid = eid.slice(7).toLowerCase();
        const st = states[eid];
        const u = (st && st.attributes && st.attributes.unit_of_measurement) || "";

        if (opts.excludeSubstrings && opts.excludeSubstrings.some((bad) => oid.includes(bad))) {
          continue;
        }

        let matchedIdx = -1;
        for (let i = 0; i < suffixes.length; i++) {
          const suf = suffixes[i];
          if (
            oid === suf ||
            oid.endsWith("_" + suf) ||
            (opts.matchAnywhere && oid.includes(suf)) ||
            ((hasZlinkyToken(oid) || oid.includes("tempo")) && oid.includes(suf))
          ) {
            matchedIdx = i;
            break;
          }
        }

        // Fallback by exact unit (e.g. "VA" for instantaneous apparent power, just like v1.2.0)
        if (matchedIdx < 0 && opts.unitFallback && u && opts.unitFallback.includes(u)) {
          matchedIdx = suffixes.length;
        }

        if (matchedIdx < 0) continue;
        if (validUnits && u && !validUnits.includes(u)) continue;

        let score = 100 - matchedIdx * 3;
        if (eid === backendAutoCandidate) score += 15;
        if (preferredPrefix && oid.startsWith(preferredPrefix)) score += 45;
        else if (oid.includes("zlinky") || oid.includes("lixee")) score += 30;
        else if (hasZlinkyToken(oid)) score += 18;

        const rawVal = st ? String(st.state ?? "") : "";
        if (rawVal && rawVal !== "unknown" && rawVal !== "unavailable" && rawVal !== "None") {
          score += 10;
          const numVal = Number(rawVal.replace(",", "."));
          if (Number.isFinite(numVal)) {
            if (opts.isPowerSlot) {
              if (u === "VA" || u === "kVA") score += 35;
              if (numVal > 0) score += 55;
              if (numVal >= 50) score += 35;
            } else if (numVal > 0) {
              score += 20;
            }
          }
        }

        if (score > bestScore) {
          bestScore = score;
          bestEid = eid;
        }
      }
      return bestEid || (backendAutoCandidate && states[backendAutoCandidate] ? backendAutoCandidate : null);
    };

    const powerSensor = findBest(
      [config.power_sensor, config.zlinky_power_sensor, zlinkyConfigured.zlinky_power_sensor],
      zlinkyResolved.zlinky_power_sensor,
      [
        "linky_puissance",
        "zlinky_apparent_power",
        "sinsts",
        "papp",
        "apparent_power",
        "puissance_apparente",
        "electrical_measurement_apparent_power",
        "puissance_instantanee",
        "sinsts1",
        "rms_power",
        "active_power",
        "shelly_power",
      ],
      ["VA", "W", "kVA", "kW"],
      {
        alwaysAllow: true,
        matchAnywhere: true,
        isPowerSlot: true,
        unitFallback: ["VA"],
        excludeSubstrings: ["smax", "pmax", "max_power", "min_power", "sinsti", "injection", "hier", "yesterday", "moyen", "average"],
      }
    );
    const currentSensor = findBest(
      [config.zlinky_current_sensor, zlinkyConfigured.zlinky_current_sensor],
      zlinkyResolved.zlinky_current_sensor,
      ["irms1", "iinst", "rms_current", "courant_efficace", "electrical_measurement_rms_current", "current_phase_1", "intensite"],
      ["A", "mA"]
    );
    const voltageSensor = findBest(
      [config.zlinky_voltage_sensor, zlinkyConfigured.zlinky_voltage_sensor],
      zlinkyResolved.zlinky_voltage_sensor,
      ["urms1", "umoy1", "rms_voltage", "tension_efficace", "electrical_measurement_rms_voltage", "voltage_phase_1"],
      ["V"]
    );
    const indexTotalSensor = findBest(
      [config.zlinky_index_total_sensor, zlinkyConfigured.zlinky_index_total_sensor],
      zlinkyResolved.zlinky_index_total_sensor,
      ["east", "base", "summation_delivered", "smartenergy_metering_summation_delivered", "energie_totale_livree", "index_total"],
      ["kWh", "Wh"]
    );
    const indexHcSensor = findBest(
      [config.zlinky_index_hc_sensor, zlinkyConfigured.zlinky_index_hc_sensor],
      zlinkyResolved.zlinky_index_hc_sensor,
      ["easf01", "hchc", "tier_1_summation_delivered", "bbrhcjb", "ejphn", "index_hc"],
      ["kWh", "Wh"]
    );
    const indexHpSensor = findBest(
      [config.zlinky_index_hp_sensor, zlinkyConfigured.zlinky_index_hp_sensor],
      zlinkyResolved.zlinky_index_hp_sensor,
      ["easf02", "hchp", "tier_2_summation_delivered", "bbrhpjb", "ejpmp", "index_hp"],
      ["kWh", "Wh"]
    );
    const ptecSensor = findBest(
      [config.zlinky_ptec_sensor, zlinkyConfigured.zlinky_ptec_sensor],
      zlinkyResolved.zlinky_ptec_sensor,
      ["ptec", "ltarf", "active_register_tier_delivered", "current_tarif", "current_tier_delivered", "periode_tarifaire", "tarif_en_cours"],
      null
    );
    const maxPowerSensor = findBest(
      [config.zlinky_max_power_sensor, zlinkyConfigured.zlinky_max_power_sensor],
      zlinkyResolved.zlinky_max_power_sensor,
      ["smaxsn", "pmax", "smaxsn1", "max_power", "daily_max_power", "puissance_max"],
      ["VA", "W", "kVA", "kW"]
    );
    const prefSensor = findBest(
      [config.zlinky_pref_sensor, zlinkyConfigured.zlinky_pref_sensor],
      zlinkyResolved.zlinky_pref_sensor,
      ["pref", "isousc", "available_power", "power_threshold", "puissance_souscrite", "pcoup"],
      ["kVA", "VA", "A", "kW"]
    );
    const dailyEnergySensor = findBest(
      [config.daily_energy_sensor, config.zlinky_daily_energy_sensor, zlinkyConfigured.zlinky_daily_energy_sensor],
      zlinkyResolved.zlinky_daily_energy_sensor,
      ["linky_energie_quotidienne", "daily_energy", "energie_quotidienne", "energie_jour", "conso_jour", "today_energy", "energy_today", "conso_quotidienne", "consommation_jour"],
      ["kWh", "Wh"],
      { alwaysAllow: true, matchAnywhere: true }
    );
    const monthlyEnergySensor = findBest(
      [config.monthly_energy_sensor],
      null,
      ["linky_energie_mensuelle", "monthly_energy", "energie_mensuelle", "energie_mois", "conso_mois", "month_energy", "energy_month"],
      ["kWh", "Wh"],
      { alwaysAllow: true, matchAnywhere: true }
    );
    const tempoTodaySensor = findBest(
      [config.tempo_today_sensor],
      null,
      ["rte_tempo_couleur_actuelle", "tempo_aujourd_hui", "tempo_today"],
      null,
      { alwaysAllow: true, matchAnywhere: true }
    );
    const tempoTomorrowSensor = findBest(
      [config.tempo_tomorrow_sensor, config.zlinky_demain_sensor, zlinkyConfigured.zlinky_demain_sensor],
      zlinkyResolved.zlinky_demain_sensor,
      ["demain", "rte_tempo_prochaine_couleur", "tempo_demain", "tempo_tomorrow", "tomorrow_color"],
      null,
      { alwaysAllow: true, matchAnywhere: true }
    );

    return {
      zlinkyEnabled,
      powerSensor,
      currentSensor,
      voltageSensor,
      indexTotalSensor,
      indexHcSensor,
      indexHpSensor,
      ptecSensor,
      maxPowerSensor,
      prefSensor,
      dailyEnergySensor,
      monthlyEnergySensor,
      tempoTodaySensor,
      tempoTomorrowSensor,
    };
  }

  /**
   * Extract account data from hass.states (handles all HA DeviceInfo slug variations + legacy names)
   */
  function extractAccountFromHass(hass, config = {}, cachedSnapshotAcc = null) {
    const states = (hass && hass.states) || {};
    const keys = Object.keys(states);
    let cc = config.compte_contrat || (cachedSnapshotAcc && cachedSnapshotAcc.compte_contrat) || "";

    if (!cc) {
      const m = keys.find((k) => /^sensor\.engie_(?:electricite_|gaz_|compte_|erl_linky_)?(\d+)_/.test(k));
      if (m) {
        cc = m.match(/^sensor\.engie_(?:electricite_|gaz_|compte_|erl_linky_)?(\d+)_/)[1];
      }
    }

    const findEngieState = (slugs) => {
      const prefixes = [
        `sensor.engie_electricite_${cc}_`,
        `sensor.engie_gaz_${cc}_`,
        `sensor.engie_compte_${cc}_`,
        `sensor.engie_erl_linky_${cc}_`,
        `sensor.engie_${cc}_`,
      ];
      for (const slug of slugs) {
        for (const pref of prefixes) {
          if (states[pref + slug]) return states[pref + slug];
        }
      }
      // Fallback via entity map if cachedSnapshotAcc has entities
      if (cachedSnapshotAcc && cachedSnapshotAcc.entities) {
        for (const slug of slugs) {
          const mappedEid = cachedSnapshotAcc.entities[slug];
          if (mappedEid && states[mappedEid]) return states[mappedEid];
        }
      }
      return null;
    };

    const getVal = (slugs, attr = null) => {
      const ent = findEngieState(Array.isArray(slugs) ? slugs : [slugs]);
      if (!ent) return null;
      if (attr) return ent.attributes ? ent.attributes[attr] : null;
      const n = Number(String(ent.state).replace(",", "."));
      return Number.isFinite(n) ? n : ent.state;
    };

    const snapEv = (cachedSnapshotAcc && cachedSnapshotAcc.elec_variation) || {};
    const snapGv = (cachedSnapshotAcc && cachedSnapshotAcc.gaz_variation) || {};
    const snapEr = (cachedSnapshotAcc && cachedSnapshotAcc.elec_releve) || {};
    const snapGr = (cachedSnapshotAcc && cachedSnapshotAcc.gaz_releve) || {};

    const elecMonthKwh = num(getVal(["consommation_electricite_mois", "elec_monthly_conso", "electricite_mois_kwh"]), num(snapEv.month_kwh, 0));
    const elecMonthEuro = num(getVal(["cout_electricite_mois", "elec_monthly_cost", "electricite_mois_euro"]), num(snapEv.month_euro, 0));
    const elecYearKwh = num(getVal(["consommation_electricite_annuelle", "elec_annual_conso", "electricite_annee_kwh"]), num(snapEv.year_kwh, 0));
    const elecYearPrevKwh = num(
      getVal(["consommation_electricite_annuelle", "elec_annual_conso", "electricite_annee_n_1_kwh"], "conso_annee_precedente") ??
        getVal(["electricite_annee_n_1_kwh"]),
      num(snapEv.year_prev_kwh, 0)
    );
    const elecVarPct = num(getVal(["variation_consommation_electricite", "elec_variation", "electricite_variation_mois"]), num(snapEv.month_variation_pct, 0));
    const elecIndex = num(getVal(["index_electricite_total", "elec_index_total", "index_electricite"]), num(snapEr.dernierIndex ?? snapEr.latest_value, 0));
    const elecIndexHc = num(getVal(["index_electricite_heures_creuses", "elec_index_hc"]), num(snapEr.dernierIndexHC, 0));
    const elecIndexHp = num(getVal(["index_electricite_heures_pleines", "elec_index_hp"]), num(snapEr.dernierIndexHP, 0));
    const elecPuissanceRaw =
      getVal(["index_electricite_total", "elec_index_total", "electricite_mois_kwh"], "puissance_souscrite") ||
      getVal(["electricite_mois_kwh"], "puissance") ||
      (cachedSnapshotAcc && cachedSnapshotAcc.elec_contract && cachedSnapshotAcc.elec_contract.puissance) ||
      "6";
    const elecPuissanceKva = parseFloat(String(elecPuissanceRaw).replace(/[^\d.]/g, "")) || 6;
    const elecTarifType =
      getVal(["index_electricite_total", "elec_index_total"], "type_comptage") ||
      getVal(["solde_a_payer", "account_solde"], "type_tarif") ||
      (cachedSnapshotAcc && cachedSnapshotAcc.elec_contract && cachedSnapshotAcc.elec_contract.typeComptage) ||
      "Heures Pleines / Heures Creuses";

    const gazMonthKwh = num(getVal(["consommation_gaz_mois_kwh", "gaz_monthly_kwh", "gaz_mois_kwh"]), num(snapGv.month_kwh, 0));
    const gazMonthEuro = num(getVal(["cout_gaz_mois", "gaz_monthly_cost", "gaz_mois_euro"]), num(snapGv.month_euro, 0));
    const gazYearKwh = num(getVal(["consommation_gaz_annuelle_kwh", "gaz_annual_kwh", "gaz_annee_kwh"]), num(snapGv.year_kwh, 0));
    const gazYearPrevKwh = num(
      getVal(["consommation_gaz_annuelle_kwh", "gaz_annual_kwh", "gaz_annee_n_1_kwh"], "conso_annee_precedente") ??
        getVal(["gaz_annee_n_1_kwh"]),
      num(snapGv.year_prev_kwh, 0)
    );
    const gazVarPct = num(getVal(["variation_consommation_gaz", "gaz_variation", "gaz_variation_mois"]), num(snapGv.month_variation_pct, 0));
    const gazIndex = num(getVal(["index_compteur_gaz", "gaz_index_m3", "index_gaz"]), num(snapGr.dernierIndex ?? snapGr.latest_value, 0));
    const gazReleveDate =
      getVal(["index_compteur_gaz", "gaz_index_m3", "index_gaz"], "date_dernier_index") ||
      getVal(["index_gaz"], "date_releve") ||
      snapGr.dateDernierIndex ||
      "";
    const gazCoeff = num(
      getVal(["coefficient_conversion_gaz", "gaz_conversion_coeff"]) ??
        getVal(["index_compteur_gaz", "gaz_index_m3"], "coefficient_conversion"),
      11.2
    );

    const solde = num(getVal(["solde_a_payer", "account_solde"]), num(cachedSnapshotAcc?.billing?.solde?.solde, 0));
    const mensualite = num(getVal(["mensualite_globale", "account_mensualite", "mensualite"]), num(cachedSnapshotAcc?.billing?.plan_mens?.montant_mensualite, 0));

    return {
      ...(cachedSnapshotAcc || {}),
      compte_contrat: cc,
      has_elec: Boolean(cachedSnapshotAcc?.has_elec || elecMonthKwh > 0 || elecYearKwh > 0 || elecIndex > 0 || findEngieState(["consommation_electricite_mois", "elec_monthly_conso", "electricite_mois_kwh"])),
      has_gaz: Boolean(cachedSnapshotAcc?.has_gaz || gazMonthKwh > 0 || gazYearKwh > 0 || gazIndex > 0 || findEngieState(["consommation_gaz_mois_kwh", "gaz_monthly_kwh", "gaz_mois_kwh"])),
      elec_contract: {
        ...((cachedSnapshotAcc && cachedSnapshotAcc.elec_contract) || {}),
        puissance: elecPuissanceKva,
        typeComptage: elecTarifType,
      },
      elec_variation: {
        ...snapEv,
        month_kwh: elecMonthKwh,
        month_euro: elecMonthEuro,
        year_kwh: elecYearKwh,
        year_prev_kwh: elecYearPrevKwh,
        month_variation_pct: elecVarPct,
      },
      gaz_variation: {
        ...snapGv,
        month_kwh: gazMonthKwh,
        month_euro: gazMonthEuro,
        year_kwh: gazYearKwh,
        year_prev_kwh: gazYearPrevKwh,
        month_variation_pct: gazVarPct,
      },
      elec_releve: {
        ...snapEr,
        dernierIndex: elecIndex,
        dernierIndexHC: elecIndexHc,
        dernierIndexHP: elecIndexHp,
        latest_value: elecIndex,
      },
      gaz_releve: {
        ...snapGr,
        dernierIndex: gazIndex,
        latest_value: gazIndex,
        latest_date: gazReleveDate,
        coeff: gazCoeff,
      },
      billing: {
        ...((cachedSnapshotAcc && cachedSnapshotAcc.billing) || {}),
        solde: { ...((cachedSnapshotAcc && cachedSnapshotAcc.billing && cachedSnapshotAcc.billing.solde) || {}), solde },
        plan_mens: { ...((cachedSnapshotAcc && cachedSnapshotAcc.billing && cachedSnapshotAcc.billing.plan_mens) || {}), montant_mensualite: mensualite },
      },
    };
  }

  function readLiveSensorValue(states, eid) {
    if (!eid || !states || !states[eid]) return { val: null, raw: "", unit: "" };
    const st = states[eid];
    const raw = String(st.state ?? "");
    if (!raw || raw === "unknown" || raw === "unavailable" || raw === "None") {
      return { val: null, raw: "", unit: (st.attributes && st.attributes.unit_of_measurement) || "" };
    }
    const n = Number(raw.replace(",", "."));
    return {
      val: Number.isFinite(n) ? n : null,
      raw,
      unit: String((st.attributes && st.attributes.unit_of_measurement) || ""),
    };
  }

  function buildMoniteurDataset(account, hass, config = {}, onHistoryReady = null) {
    const companions = autoDetectCompanionSensors(hass, config, account);
    const liveHist = ensureLive24hHistory(hass, companions, onHistoryReady);
    const states = (hass && hass.states) || {};
    const zSnapLive = (account.zlinky && account.zlinky.live) || {};
    const now = new Date();
    const currentMonthIdx = now.getMonth();
    const currentYear = now.getFullYear();
    const dayOfMonth = Math.max(1, now.getDate());
    const daysInMonth = new Date(currentYear, currentMonthIdx + 1, 0).getDate();
    const currentYm = `${currentYear}-${String(currentMonthIdx + 1).padStart(2, "0")}`;

    const ev = account.elec_variation || {};
    const gv = account.gaz_variation || {};
    const er = account.elec_releve || {};

    const evMonthKwh = num(ev.month_kwh, 0);
    const evMonthEuro = num(ev.month_euro, 0);
    const elecYearKwh = num(ev.year_kwh, 0);
    const elecYearPrevKwh = num(ev.year_prev_kwh, 0);
    const elecAvgPrice = evMonthKwh > 0 && evMonthEuro > 0 ? evMonthEuro / evMonthKwh : 0.21;

    // Reference daily average: ev.month_kwh from ENGIE getConsumptionVariation is a full ~30-day month
    const refDailyAvg = evMonthKwh > 0 ? evMonthKwh / 30 : elecYearKwh > 0 ? elecYearKwh / 365 : 10.9;

    // 1. Live ZLinky_TIC / Subscribed Power (PREF / ISOUSC)
    let maxKva = parseFloat(String((account.elec_contract && account.elec_contract.puissance) || "6").replace(/[^\d.]/g, "")) || 6;
    const prefRead = readLiveSensorValue(states, companions.prefSensor);
    if (prefRead.val !== null && prefRead.val > 0) {
      const pu = prefRead.unit.toLowerCase();
      if (pu === "a") maxKva = Math.round(prefRead.val * 0.2 * 10) / 10;
      else if (pu === "va" || pu === "w" || prefRead.val >= 1000) maxKva = Math.round((prefRead.val / 1000) * 10) / 10;
      else maxKva = prefRead.val;
    } else if (Number.isFinite(Number(zSnapLive.pref_kva)) && Number(zSnapLive.pref_kva) > 0) {
      maxKva = Number(zSnapLive.pref_kva);
    }
    const maxVA = maxKva * 1000;

    // 2. Live ZLinky_TIC Apparent Power (SINSTS / PAPP)
    let powerVA = Math.round((refDailyAvg * 1000) / 24);
    let powerIsLive = false;
    const pRead = readLiveSensorValue(states, companions.powerSensor);
    if (pRead.val !== null) {
      const pu = pRead.unit.toLowerCase();
      powerVA = pu === "kw" || pu === "kva" ? Math.round(pRead.val * 1000) : Math.round(pRead.val);
      powerIsLive = true;
    } else if (Number.isFinite(Number(zSnapLive.power_va)) && zSnapLive.power_va !== null) {
      powerVA = Math.round(Number(zSnapLive.power_va));
      powerIsLive = true;
    }

    // 3. Live ZLinky_TIC Current (A), Voltage (V), Max Power Day (VA), PTEC & Indexes
    const curRead = readLiveSensorValue(states, companions.currentSensor);
    const currentA =
      curRead.val !== null
        ? curRead.unit.toLowerCase() === "ma"
          ? Number((curRead.val / 1000).toFixed(2))
          : Number(curRead.val.toFixed(2))
        : zSnapLive.current_a ?? null;

    const voltRead = readLiveSensorValue(states, companions.voltageSensor);
    const voltageV = voltRead.val !== null ? Number(voltRead.val.toFixed(1)) : zSnapLive.voltage_v ?? null;

    const maxPRead = readLiveSensorValue(states, companions.maxPowerSensor);
    const maxPowerDayVA =
      maxPRead.val !== null
        ? maxPRead.unit.toLowerCase() === "kva" || maxPRead.unit.toLowerCase() === "kw"
          ? Math.round(maxPRead.val * 1000)
          : Math.round(maxPRead.val)
        : zSnapLive.max_power_va ?? null;

    const ptecRead = readLiveSensorValue(states, companions.ptecSensor);
    const ptecLabel = ptecRead.raw || zSnapLive.ptec || null;
    const ptecIsHc = isHcFromPtec(ptecLabel);
    const isHcNow = ptecIsHc !== null ? ptecIsHc : isHcHour(now.getHours());

    const toKwh = (r, snapFallback) => {
      if (r.val !== null) {
        return r.unit.toLowerCase() === "wh" ? Number((r.val / 1000).toFixed(3)) : Number(r.val.toFixed(3));
      }
      return Number.isFinite(Number(snapFallback)) && snapFallback !== null ? Number(snapFallback) : null;
    };

    const idxHcKwh = toKwh(readLiveSensorValue(states, companions.indexHcSensor), zSnapLive.index_hc_kwh);
    const idxHpKwh = toKwh(readLiveSensorValue(states, companions.indexHpSensor), zSnapLive.index_hp_kwh);
    let idxTotalKwh = toKwh(readLiveSensorValue(states, companions.indexTotalSensor), zSnapLive.index_total_kwh);
    if (idxTotalKwh === null && idxHcKwh !== null && idxHpKwh !== null) {
      idxTotalKwh = Number((idxHcKwh + idxHpKwh).toFixed(3));
    }

    // 4. Today Energy (kWh) & Today HC (kWh):
    // Priority: 1) dailyEnergySensor, 2) Midnight-to-now HA history delta, 3) Power history integration, 4) Reference daily avg
    let todayKwh = Number(refDailyAvg.toFixed(1));
    let todayIsLive = false;
    let todayHcKwh = Number((todayKwh * 0.4).toFixed(1));

    const dRead = readLiveSensorValue(states, companions.dailyEnergySensor);
    if (dRead.val !== null && dRead.val > 0) {
      todayKwh = dRead.unit.toLowerCase() === "wh" ? dRead.val / 1000 : dRead.val;
      todayIsLive = true;
    } else if (liveHist.todayDeltaTotalKwh !== null && liveHist.todayDeltaTotalKwh > 0) {
      todayKwh = liveHist.todayDeltaTotalKwh;
      todayIsLive = true;
    } else if (liveHist.todayFromPowerKwh !== null && liveHist.todayFromPowerKwh > 0) {
      todayKwh = liveHist.todayFromPowerKwh;
      todayIsLive = true;
    } else if (powerIsLive && powerVA > 0) {
      // Live power sensor available before WS history resolves: estimate elapsed today consumption from live power & ref avg
      const elapsedHours = Math.max(1, now.getHours() + now.getMinutes() / 60);
      const liveHourlyKwh = powerVA / 1000;
      todayKwh = Number((((liveHourlyKwh * 0.65) + ((refDailyAvg / 24) * 0.35)) * elapsedHours).toFixed(1));
      todayIsLive = true;
    }

    if (liveHist.todayDeltaHcKwh !== null && liveHist.todayDeltaHcKwh >= 0) {
      todayHcKwh = liveHist.todayDeltaHcKwh;
    } else if (liveHist.todayHcFromPowerKwh !== null && liveHist.todayHcFromPowerKwh >= 0) {
      todayHcKwh = liveHist.todayHcFromPowerKwh;
    } else {
      todayHcKwh = Number((todayKwh * 0.4).toFixed(1));
    }

    // 5. Current Month Cumulative (CUMUL MOIS) & End-of-Month Forecast
    let elecMonthKwh = 0;
    const mRead = readLiveSensorValue(states, companions.monthlyEnergySensor);
    if (mRead.val !== null && mRead.val > 0) {
      elecMonthKwh = mRead.unit.toLowerCase() === "wh" ? Math.round(mRead.val / 1000) : Math.round(mRead.val);
    } else if (Array.isArray(account.elec_daily) && account.elec_daily.length > 0) {
      const curMonthDays = account.elec_daily.filter((d) => String(d.date || d.valeurDate || "").startsWith(currentYm));
      if (curMonthDays.length > 0) {
        const sumDays = curMonthDays.reduce((acc, d) => acc + num(d.kwh ?? d.curr ?? d.conso, 0), 0);
        elecMonthKwh = Math.round(sumDays + (todayIsLive ? todayKwh : 0));
      }
    }
    if (elecMonthKwh <= 0) {
      // Estimate current month-to-date from completed days of current month + todayKwh
      elecMonthKwh = Math.round(refDailyAvg * Math.max(0, dayOfMonth - 1) + todayKwh);
    }
    const elecMonthEuro = Math.round(elecMonthKwh * elecAvgPrice * 100) / 100;
    const elecDailyAvg = elecMonthKwh > 0 ? elecMonthKwh / dayOfMonth : refDailyAvg;
    const elecForecastMonth = elecMonthKwh > 0 ? Math.round((elecMonthKwh / dayOfMonth) * daysInMonth) : 301;

    const todayCost = todayKwh * elecAvgPrice;
    const zlinkyActive = Boolean(
      powerIsLive || currentA !== null || voltageV !== null || idxTotalKwh !== null || ptecLabel
    );

    // 6. 24h Hourly Load Curve:
    // Priority: 1) Live HA Recorder 24h history of powerSensor / indexSensor, 2) ENGIE elec_hourly, 3) Normalized profile up to currentHour
    const currentHour = now.getHours();
    const realHourly = Array.isArray(account.elec_hourly) ? account.elec_hourly : [];
    const hourlyPoints = [];
    let hourlyIsLiveHistory = false;

    if (Array.isArray(liveHist.hourlyKwh) && liveHist.hourlyKwh.length === 24) {
      hourlyIsLiveHistory = true;
      for (let h = 0; h < 24; h++) {
        let kwh = h <= currentHour ? num(liveHist.hourlyKwh[h], 0) : 0;
        if (h === currentHour && powerIsLive && powerVA > 0 && kwh <= 0) {
          kwh = Number((powerVA / 1000).toFixed(2));
        }
        const euro = Number((kwh * (isHcHour(h) ? elecAvgPrice * 0.78 : elecAvgPrice * 1.12)).toFixed(3));
        hourlyPoints.push({
          label: `${String(h).padStart(2, "0")}:00`,
          hour: h,
          kwh,
          euro,
          isHc: isHcHour(h),
        });
      }
    } else if (realHourly.length >= 12) {
      for (let i = 0; i < 24; i++) {
        const pt = realHourly[i] || {};
        const h = Number.isFinite(Number(pt.hour)) ? Number(pt.hour) : i;
        const kwh = num(pt.kwh ?? pt.conso ?? pt.value, 0);
        const euro = num(pt.euro ?? pt.consoEuro, kwh * elecAvgPrice);
        hourlyPoints.push({
          label: `${String(h).padStart(2, "0")}:00`,
          hour: h,
          kwh,
          euro,
          isHc: isHcHour(h),
        });
      }
    } else {
      let activeProfileSum = 0;
      for (let h = 0; h <= currentHour; h++) activeProfileSum += HOURLY_PROFILE[h];
      const normActive = activeProfileSum > 0 ? 1 / activeProfileSum : 1;

      for (let h = 0; h < 24; h++) {
        const active = h <= currentHour;
        let kwh = active ? Number((todayKwh * HOURLY_PROFILE[h] * normActive).toFixed(2)) : 0;
        if (h === currentHour && powerIsLive && powerVA > 0) {
          kwh = Number((powerVA / 1000).toFixed(2));
        }
        const euro = Number((kwh * (isHcHour(h) ? elecAvgPrice * 0.78 : elecAvgPrice * 1.12)).toFixed(3));
        hourlyPoints.push({
          label: `${String(h).padStart(2, "0")}:00`,
          hour: h,
          kwh,
          euro,
          isHc: isHcHour(h),
        });
      }
    }

    // 6. 14-day Daily Elec & Gaz (supports both normalized {kwh, curr, prev} and raw ENGIE {valeurDate, conso})
    const formatShortDate = (raw) => {
      const s = String(raw || "").trim();
      if (s.length >= 10 && s[4] === "-" && s[7] === "-") return `${s.slice(8, 10)}/${s.slice(5, 7)}`;
      return s.slice(0, 5);
    };

    const buildDaily14 = (realDaily, baseKwh, varPct) => {
      if (Array.isArray(realDaily) && realDaily.length >= 5) {
        return realDaily.slice(-14).map((d) => {
          const c = num(d.kwh ?? d.curr ?? d.conso ?? d.value, 0);
          const p = num(d.prev ?? d.kwh_prev ?? d.consoN1 ?? d.consoAnneePrecedente, Number((c * 1.05).toFixed(1)));
          return {
            label: formatShortDate(d.label || d.valeurDate || d.dateDebut || d.date || ""),
            curr: c,
            prev: p,
          };
        });
      }
      const pts = [];
      const ratioPrev = 1 - num(varPct, 0) / 100;
      const safePrevRatio = ratioPrev > 0.4 && ratioPrev < 2.0 ? ratioPrev : 1.05;
      const pattern = [1.12, 1.08, 0.94, 0.96, 0.98, 0.72, 0.76, 0.74, 0.73, 1.04, 0.80, 0.88, 0.95, 1.10];
      for (let i = 13; i >= 0; i--) {
        const dt = new Date(now.getTime() - i * 86400000);
        const lbl = `${String(dt.getDate()).padStart(2, "0")}/${String(dt.getMonth() + 1).padStart(2, "0")}`;
        const factor = pattern[13 - i];
        const c = Number((baseKwh * factor).toFixed(1));
        const p = Number((c * safePrevRatio * (0.95 + ((i * 7) % 10) / 100)).toFixed(1));
        pts.push({ label: lbl, curr: c, prev: p });
      }
      return pts;
    };

    const elecDailyAvgEffective = elecMonthKwh > 0 ? elecMonthKwh / dayOfMonth : 14.5;
    const gazMonthKwh = num(gv.month_kwh, 0);
    const gazMonthEuro = num(gv.month_euro, 0);
    const gazYearKwh = num(gv.year_kwh, 0);
    const gazYearPrevKwh = num(gv.year_prev_kwh, 0);
    const gazDailyAvg = gazMonthKwh > 0 ? gazMonthKwh / dayOfMonth : 31.9;
    const gazAvgPrice = gazMonthKwh > 0 && gazMonthEuro > 0 ? gazMonthEuro / gazMonthKwh : 0.11;
    const gazCoeff = num(account.gaz_releve && account.gaz_releve.coeff, 11.2);
    const gazM3 = Number((gazDailyAvg / gazCoeff).toFixed(1));

    const elecDaily14 = buildDaily14(account.elec_daily, elecDailyAvgEffective, ev.month_variation_pct);
    const gazDaily14 = buildDaily14(account.gaz_daily, gazDailyAvg, gv.month_variation_pct);

    // 7. 12-month Monthly Elec & Gaz
    const buildMonthly12 = (realMonthly, seasonal, yearN, yearN1, curMonthVal) => {
      if (Array.isArray(realMonthly) && realMonthly.length >= 6) {
        return realMonthly.slice(0, 12).map((m, idx) => {
          const c = num(m.curr ?? m.kwh ?? m.conso, 0);
          const p = num(m.prev ?? m.kwh_prev ?? m.consoN1 ?? m.consoAnneePrecedente, Math.round(c * 1.04));
          return {
            label: m.label || MONTHS_FR[idx] || `M${idx + 1}`,
            curr: c,
            prev: p,
          };
        });
      }
      const nTotal = yearN > 0 ? yearN : 4200;
      const n1Total = yearN1 > 0 ? yearN1 : Math.round(nTotal * 1.04);
      let activeSum = 0;
      for (let i = 0; i <= currentMonthIdx; i++) activeSum += seasonal[i];
      const normFactor = activeSum > 0 ? 1 / activeSum : 1;

      return MONTHS_FR.map((lbl, idx) => {
        const prev = Math.round(n1Total * seasonal[idx]);
        let curr = 0;
        if (idx < currentMonthIdx) {
          curr = Math.round(nTotal * seasonal[idx] * normFactor * 0.88);
        } else if (idx === currentMonthIdx) {
          curr = curMonthVal > 0 ? Math.round(curMonthVal) : Math.round(nTotal * seasonal[idx] * normFactor * 0.88);
        }
        return { label: lbl, curr, prev };
      });
    };

    const elecMonthly12 = buildMonthly12(account.elec_monthly, ELEC_SEASONAL, elecYearKwh, elecYearPrevKwh, elecMonthKwh);
    const gazMonthly12 = buildMonthly12(account.gaz_monthly, GAZ_SEASONAL, gazYearKwh, gazYearPrevKwh, gazMonthKwh);

    // 8. Tempo detection
    let tempoToday = null;
    let tempoTomorrow = null;
    if (companions.tempoTodaySensor && states[companions.tempoTodaySensor]) {
      tempoToday = String(states[companions.tempoTodaySensor].state || "").toUpperCase();
    } else if (ptecLabel && (ptecLabel.toUpperCase().includes("BLEU") || ptecLabel.toUpperCase().includes("BLANC") || ptecLabel.toUpperCase().includes("ROUGE"))) {
      tempoToday = ptecLabel.toUpperCase();
    }
    if (companions.tempoTomorrowSensor && states[companions.tempoTomorrowSensor]) {
      tempoTomorrow = String(states[companions.tempoTomorrowSensor].state || "").toUpperCase();
    } else if (zSnapLive.demain) {
      tempoTomorrow = String(zSnapLive.demain).toUpperCase();
    }

    const isCalibrated = !(Array.isArray(account.elec_daily) && account.elec_daily.length >= 5) && !zlinkyActive;

    return {
      currentYear,
      prevYear: currentYear - 1,
      companions,
      isCalibrated,
      zlinkyActive,
      elec: {
        powerVA,
        maxVA,
        maxKva,
        powerPct: Math.min(100, Math.round((powerVA / Math.max(100, maxVA)) * 100)),
        powerIsLive,
        currentA,
        voltageV,
        maxPowerDayVA,
        ptecLabel,
        idxTotalKwh,
        idxHcKwh,
        idxHpKwh,
        todayKwh,
        todayIsLive,
        todayHcKwh,
        todayCost,
        avgHourlyCost: todayCost / 24,
        monthKwh: elecMonthKwh,
        monthEuro: elecMonthEuro,
        forecastMonth: elecForecastMonth,
        yearKwh: elecYearKwh,
        yearPrevKwh: elecYearPrevKwh,
        hourlyPoints,
        daily14: elecDaily14,
        monthly12: elecMonthly12,
        isHcNow,
        tarifType: ptecLabel || (account.elec_contract && account.elec_contract.typeComptage) || "Heures Pleines / Creuses",
        tempoToday,
        tempoTomorrow,
      },
      gaz: {
        lastDayKwh: Number(gazDailyAvg.toFixed(1)),
        lastDate: (account.gaz_releve && (account.gaz_releve.latest_date || account.gaz_releve.dateDernierIndex)) || `${String(dayOfMonth).padStart(2, "0")}/${String(currentMonthIdx + 1).padStart(2, "0")}`,
        monthKwh: gazMonthKwh,
        monthEuro: gazMonthEuro > 0 ? gazMonthEuro : Math.round(gazMonthKwh * gazAvgPrice),
        avgPrice: gazAvgPrice,
        m3: gazM3,
        coeff: gazCoeff,
        yearKwh: gazYearKwh,
        yearPrevKwh: gazYearPrevKwh,
        daily14: gazDaily14,
        monthly12: gazMonthly12,
      },
    };
  }

  function renderLivePowerGaugeSVG(powerVA, pct, isHcNow, ptecLabel) {
    const color = pct < 50 ? "#10b981" : pct < 80 ? "#f59e0b" : "#ef4444";
    const dashLen = 251.2;
    const offset = dashLen - (dashLen * Math.min(100, Math.max(2, pct))) / 100;
    const tarifText = ptecLabel ? esc(ptecLabel) : (isHcNow ? "Heures Creuses" : "Heures Pleines");
    return `
      <div class="dlm-gauge-wrap">
        <svg viewBox="0 0 200 112" class="dlm-semi-svg">
          <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="rgba(148,163,184,0.16)" stroke-width="14" stroke-linecap="round" />
          <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="${color}" stroke-width="14" stroke-linecap="round"
            stroke-dasharray="${dashLen}" stroke-dashoffset="${offset}" style="transition: stroke-dashoffset .6s ease;" />
          <text x="100" y="92" text-anchor="middle" fill="#94a3b8" font-size="11" font-weight="600" letter-spacing="0.06em">${pct}% CAPACITÉ</text>
        </svg>
        <div class="dlm-tarif-row">
          <span>Tarif en cours:</span>
          <strong style="color:${isHcNow ? "#34d399" : "#fb923c"};">${tarifText}</strong>
        </div>
      </div>
    `;
  }

  function renderZLinkyTelemetryStrip(e) {
    const pills = [];
    if (e.voltageV !== null) {
      pills.push(`<span class="dlm-zpill">🔌 Tension : <b>${fmt(e.voltageV, 0)} V</b></span>`);
    }
    if (e.currentA !== null) {
      pills.push(`<span class="dlm-zpill">⚡ Intensité : <b>${fmt(e.currentA, 1)} A</b></span>`);
    }
    if (e.maxPowerDayVA !== null) {
      pills.push(`<span class="dlm-zpill">📈 Pic Jour : <b>${fmtInt(e.maxPowerDayVA)} VA</b></span>`);
    }
    if (e.idxTotalKwh !== null) {
      pills.push(`<span class="dlm-zpill">📟 Index Total : <b>${fmt(e.idxTotalKwh, 1)} kWh</b></span>`);
    }
    if (e.idxHcKwh !== null && e.idxHpKwh !== null) {
      pills.push(`<span class="dlm-zpill">🌙 HC : <b>${fmt(e.idxHcKwh, 1)}</b> · ☀️ HP : <b>${fmt(e.idxHpKwh, 1)} kWh</b></span>`);
    }
    if (!pills.length) return "";
    return `
      <div class="dlm-zstrip">
        <span class="dlm-zbadge"><span class="dlm-dot" style="background:#22c55e"></span>ZLINKY_TIC LIVE</span>
        <div class="dlm-zpills">${pills.join("")}</div>
      </div>
    `;
  }

  function renderGasRingGaugeSVG(kwhVal) {
    const r = 52;
    const circ = 2 * Math.PI * r;
    const pct = Math.min(100, Math.max(12, Math.round((kwhVal / 80) * 100)));
    const offset = circ - (circ * pct) / 100;
    return `
      <div class="dlm-gas-ring-wrap">
        <svg viewBox="0 0 132 132" class="dlm-ring-svg">
          <circle cx="66" cy="66" r="${r}" fill="none" stroke="rgba(148,163,184,0.16)" stroke-width="10" />
          <circle cx="66" cy="66" r="${r}" fill="none" stroke="#f97316" stroke-width="10" stroke-linecap="round"
            stroke-dasharray="${circ}" stroke-dashoffset="${offset}" transform="rotate(-90 66 66)" />
          <text x="66" y="54" text-anchor="middle" font-size="18">🔥</text>
          <text x="66" y="76" text-anchor="middle" fill="#f8fafc" font-size="17" font-weight="800">${Math.round(kwhVal)}</text>
          <text x="66" y="92" text-anchor="middle" fill="#94a3b8" font-size="9" font-weight="700" letter-spacing="0.08em">KWH (J-1)</text>
        </svg>
        <div class="dlm-gazpar-pill">GAZPAR</div>
      </div>
    `;
  }

  function renderLoadCurve24hSVG(hourlyPoints) {
    const W = 760, H = 250;
    const padL = 42, padR = 48, padT = 18, padB = 30;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;

    const maxKwh = Math.max(1.2, ...hourlyPoints.map((p) => p.kwh)) * 1.15;
    const maxEuro = Math.max(0.24, ...hourlyPoints.map((p) => p.euro)) * 1.15;

    let grid = "";
    for (let i = 0; i <= 4; i++) {
      const y = padT + (chartH * i) / 4;
      const kwhTick = ((maxKwh * (4 - i)) / 4).toFixed(1);
      const eurTick = ((maxEuro * (4 - i)) / 4).toFixed(2);
      grid += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(148,163,184,0.1)" stroke-dasharray="3 3" />`;
      grid += `<text x="${padL - 8}" y="${y + 4}" text-anchor="end" fill="#64748b" font-size="10">${kwhTick}</text>`;
      grid += `<text x="${W - padR + 8}" y="${y + 4}" text-anchor="start" fill="#64748b" font-size="10">€${eurTick}</text>`;
    }

    const slotW = chartW / 24;
    const barW = Math.max(8, slotW * 0.56);
    let bars = "";
    const linePts = [];

    hourlyPoints.forEach((p, idx) => {
      const cx = padL + idx * slotW + slotW / 2;
      const bh = Math.max(0, (p.kwh / maxKwh) * chartH);
      const bx = cx - barW / 2;
      const by = padT + chartH - bh;
      const fill = p.isHc ? "#3b82f6" : "#64748b";
      if (bh > 0.5) {
        bars += `<rect x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${barW.toFixed(1)}" height="${bh.toFixed(1)}" rx="3" fill="${fill}" opacity="0.9">
          <title>${p.label} — ${fmt(p.kwh, 2)} kWh (${fmt(p.euro, 3)} €) • ${p.isHc ? "Heures Creuses" : "Heures Pleines"}</title>
        </rect>`;
      }
      const ly = padT + chartH - (p.euro / maxEuro) * chartH;
      linePts.push({ x: cx, y: ly });
      if (idx % 4 === 0) {
        bars += `<text x="${cx.toFixed(1)}" y="${H - 8}" text-anchor="middle" fill="#64748b" font-size="10">${p.label}</text>`;
      }
    });

    let pathD = "";
    linePts.forEach((pt, idx) => {
      if (idx === 0) pathD += `M ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`;
      else {
        const prev = linePts[idx - 1];
        const mx = (prev.x + pt.x) / 2;
        pathD += ` C ${mx.toFixed(1)} ${prev.y.toFixed(1)}, ${mx.toFixed(1)} ${pt.y.toFixed(1)}, ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`;
      }
    });

    return `
      <svg viewBox="0 0 ${W} ${H}" class="dlm-chart-svg">
        ${grid}
        ${bars}
        <path d="${pathD}" fill="none" stroke="#ef4444" stroke-width="2.2" stroke-linecap="round" />
      </svg>
    `;
  }

  function renderComparativeBarChartSVG(points, currColor, prevColor, currLabel, prevLabel) {
    const W = 760, H = 250;
    const padL = 44, padR = 16, padT = 16, padB = 30;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;

    const maxVal = Math.max(10, ...points.flatMap((p) => [p.curr, p.prev])) * 1.15;
    let grid = "";
    for (let i = 0; i <= 4; i++) {
      const y = padT + (chartH * i) / 4;
      const tick = Math.round((maxVal * (4 - i)) / 4);
      grid += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(148,163,184,0.1)" stroke-dasharray="3 3" />`;
      grid += `<text x="${padL - 8}" y="${y + 4}" text-anchor="end" fill="#64748b" font-size="10">${tick}</text>`;
    }

    const n = Math.max(1, points.length);
    const slotW = chartW / n;
    const pairW = Math.min(34, slotW * 0.68);
    const barW = Math.max(6, pairW / 2 - 1.5);
    let bars = "";

    points.forEach((p, idx) => {
      const cx = padL + idx * slotW + slotW / 2;
      const hPrev = Math.max(0, (p.prev / maxVal) * chartH);
      const hCurr = Math.max(0, (p.curr / maxVal) * chartH);
      const xLeft = cx - barW - 1.5;
      const xRight = cx + 1.5;

      // Left bar: Previous Year / N-1 (2025)
      if (hPrev > 0.5) {
        bars += `<rect x="${xLeft.toFixed(1)}" y="${(padT + chartH - hPrev).toFixed(1)}" width="${barW.toFixed(1)}" height="${hPrev.toFixed(1)}" rx="3" fill="${prevColor}" opacity="0.88">
          <title>${p.label} — ${prevLabel}: ${fmt(p.prev, 1)} kWh | ${currLabel}: ${fmt(p.curr, 1)} kWh</title>
        </rect>`;
      }
      // Right bar: Current Period / N (2026)
      if (hCurr > 0.5) {
        bars += `<rect x="${xRight.toFixed(1)}" y="${(padT + chartH - hCurr).toFixed(1)}" width="${barW.toFixed(1)}" height="${hCurr.toFixed(1)}" rx="3" fill="${currColor}">
          <title>${p.label} — ${prevLabel}: ${fmt(p.prev, 1)} kWh | ${currLabel}: ${fmt(p.curr, 1)} kWh</title>
        </rect>`;
      }
      bars += `<text x="${cx.toFixed(1)}" y="${H - 8}" text-anchor="middle" fill="#64748b" font-size="10">${esc(p.label)}</text>`;
    });

    return `
      <svg viewBox="0 0 ${W} ${H}" class="dlm-chart-svg">
        ${grid}
        ${bars}
      </svg>
      <div class="dlm-legend">
        <span><i style="background:${prevColor}"></i>${esc(prevLabel)}</span>
        <span><i style="background:${currColor}"></i>${esc(currLabel)}</span>
      </div>
    `;
  }

  const MONITEUR_CSS = `
    .dlm-root {
      --bg-card: rgba(15, 23, 42, 0.88);
      --bg-card-border: rgba(148, 163, 184, 0.14);
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      color: var(--text-main);
      font-family: var(--paper-font-body1_-_font-family, system-ui, -apple-system, sans-serif);
      background: radial-gradient(circle at 50% 0%, rgba(59, 130, 246, 0.12), transparent 55%), #090d16;
      border: 1px solid var(--bg-card-border);
      border-radius: 24px;
      padding: 20px;
      box-sizing: border-box;
    }
    .dlm-header {
      display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center;
      gap: 14px; margin-bottom: 16px;
    }
    .dlm-title-row { display: flex; align-items: center; gap: 10px; }
    .dlm-title-icon { font-size: 24px; line-height: 1; }
    .dlm-title { font-size: 20px; font-weight: 800; letter-spacing: -0.02em; margin: 0; }
    .dlm-subtitle {
      display: flex; align-items: center; gap: 6px;
      font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
      color: var(--text-muted); margin-top: 2px;
    }
    .dlm-dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
    .dlm-controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .dlm-pill-group {
      display: inline-flex; background: rgba(15, 23, 42, 0.9);
      border: 1px solid rgba(148, 163, 184, 0.16); border-radius: 14px; padding: 4px; gap: 4px;
    }
    .dlm-tab-btn {
      border: 0; background: transparent; color: #94a3b8; font-size: 12px; font-weight: 700;
      padding: 7px 13px; border-radius: 10px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
      transition: all .15s ease;
    }
    .dlm-tab-btn:hover { color: #f8fafc; }
    .dlm-tab-btn.active-linky { background: #16a34a; color: #fff; box-shadow: 0 2px 10px rgba(22,163,74,.35); }
    .dlm-tab-btn.active-elec { background: #2563eb; color: #fff; box-shadow: 0 2px 10px rgba(37,99,235,.35); }
    .dlm-tab-btn.active-gas { background: #ea580c; color: #fff; box-shadow: 0 2px 10px rgba(234,88,12,.35); }
    .dlm-period-btn {
      border: 0; background: transparent; color: #94a3b8; font-size: 12px; font-weight: 700;
      padding: 7px 11px; border-radius: 10px; cursor: pointer; display: inline-flex; align-items: center; gap: 5px;
    }
    .dlm-period-btn.active { background: rgba(51, 65, 85, 0.85); color: #fff; }
    .dlm-banner {
      display: flex; align-items: center; gap: 8px; padding: 9px 14px; border-radius: 12px;
      background: rgba(234, 88, 12, 0.1); border: 1px solid rgba(234, 88, 12, 0.28);
      color: #fdba74; font-size: 12px; margin-bottom: 16px;
    }
    .dlm-banner.info {
      background: rgba(37, 99, 235, 0.1); border-color: rgba(59, 130, 246, 0.25); color: #93c5fd;
    }
    .dlm-zstrip {
      display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;
      padding: 10px 14px; border-radius: 14px; margin-bottom: 16px;
      background: linear-gradient(90deg, rgba(16, 185, 129, 0.12), rgba(15, 23, 42, 0.85));
      border: 1px solid rgba(16, 185, 129, 0.3);
    }
    .dlm-zbadge {
      display: inline-flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 800;
      color: #34d399; letter-spacing: 0.06em;
    }
    .dlm-zpills { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .dlm-zpill {
      font-size: 11px; color: #cbd5e1; background: rgba(15, 23, 42, 0.75);
      border: 1px solid rgba(148, 163, 184, 0.18); padding: 4px 10px; border-radius: 999px;
    }
    .dlm-zpill b { color: #f8fafc; }
    .dlm-kpi-grid { display: grid; gap: 14px; margin-bottom: 16px; }
    .dlm-kpi-grid.cols-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .dlm-kpi-grid.cols-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    @media (max-width: 960px) {
      .dlm-kpi-grid.cols-4, .dlm-kpi-grid.cols-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 560px) {
      .dlm-kpi-grid.cols-4, .dlm-kpi-grid.cols-3 { grid-template-columns: 1fr; }
    }
    .dlm-kpi-card {
      background: linear-gradient(145deg, rgba(22, 30, 46, 0.92), rgba(13, 18, 30, 0.95));
      border: 1px solid var(--bg-card-border); border-radius: 22px; padding: 16px 18px;
      display: flex; flex-direction: column; justify-content: space-between; min-height: 132px;
    }
    .dlm-kpi-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
    .dlm-kpi-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; }
    .dlm-kpi-val { font-size: 28px; font-weight: 800; line-height: 1.15; margin-top: 6px; color: #f8fafc; }
    .dlm-kpi-val span.unit { font-size: 14px; font-weight: 600; color: #94a3b8; margin-left: 3px; }
    .dlm-icon-box {
      width: 36px; height: 36px; border-radius: 12px; display: flex; align-items: center; justify-content: center;
      font-size: 16px; flex-shrink: 0;
    }
    .dlm-kpi-foot { font-size: 11px; color: #94a3b8; margin-top: 12px; }
    .dlm-gauge-wrap { display: flex; flex-direction: column; align-items: center; margin-top: 4px; }
    .dlm-semi-svg { width: 155px; height: 86px; }
    .dlm-tarif-row {
      width: 100%; display: flex; justify-content: space-between; font-size: 11px;
      color: #94a3b8; margin-top: 4px;
    }
    .dlm-gas-ring-wrap { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; }
    .dlm-ring-svg { width: 112px; height: 112px; }
    .dlm-gazpar-pill {
      font-size: 10px; font-weight: 800; letter-spacing: 0.08em; color: #fb923c;
      background: rgba(234, 88, 12, 0.16); border: 1px solid rgba(234, 88, 12, 0.35);
      padding: 3px 10px; border-radius: 999px;
    }
    .dlm-chart-card {
      background: linear-gradient(180deg, rgba(17, 24, 39, 0.92), rgba(11, 16, 27, 0.96));
      border: 1px solid var(--bg-card-border); border-radius: 22px; padding: 18px 20px;
    }
    .dlm-chart-title { font-size: 15px; font-weight: 700; margin: 0 0 2px; }
    .dlm-chart-sub { font-size: 11px; color: #64748b; margin-bottom: 12px; }
    .dlm-chart-svg { width: 100%; height: auto; display: block; }
    .dlm-legend {
      display: flex; justify-content: center; gap: 18px; margin-top: 10px;
      font-size: 11px; font-weight: 600; color: #cbd5e1;
    }
    .dlm-legend span { display: inline-flex; align-items: center; gap: 6px; }
    .dlm-legend i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
    .dlm-tempo-card {
      margin-top: 16px; background: linear-gradient(145deg, rgba(22, 30, 46, 0.9), rgba(13, 18, 30, 0.95));
      border: 1px solid var(--bg-card-border); border-radius: 20px; padding: 14px 18px;
      display: inline-flex; flex-direction: column; min-width: 260px; max-width: 100%;
    }
    .dlm-tempo-head { display: flex; justify-content: space-between; align-items: center; gap: 18px; margin-bottom: 10px; }
    .dlm-tempo-badge {
      font-size: 10px; font-weight: 800; color: #60a5fa;
      background: rgba(37, 99, 235, 0.16); border: 1px solid rgba(59, 130, 246, 0.35);
      padding: 2px 8px; border-radius: 8px;
    }
    .dlm-tempo-cols { display: flex; gap: 28px; align-items: center; flex-wrap: wrap; }
    .dlm-tempo-day { font-size: 10px; color: #64748b; font-weight: 700; text-transform: uppercase; margin-bottom: 4px; }
    .dlm-tempo-val { font-size: 13px; font-weight: 800; display: flex; align-items: center; gap: 6px; }
  `;

  function renderMoniteurHTML(account, hass, state, config = {}, onHistoryReady = null) {
    const data = buildMoniteurDataset(account, hass, config, onHistoryReady);
    const activeTab = state.tab || "linky"; // linky | elec | gas
    const period = activeTab === "linky" ? "hourly" : (state.period === "monthly" ? "monthly" : "daily");

    const isGaz = activeTab === "gas";
    const headerIcon = isGaz ? "🔥" : "⚡";
    const subDotColor = isGaz ? "#f97316" : "#22c55e";
    const subLabel = isGaz
      ? "GRDF GAZPAR"
      : data.zlinkyActive
      ? "ENEDIS LINKY · ZLINKY_TIC TEMPS RÉEL"
      : "ENEDIS LINKY";

    const periodControls = activeTab === "linky"
      ? `<button class="dlm-period-btn active" data-dlm-period="hourly" title="Courbe 24 heures">⏳ 24h</button>`
      : `<button class="dlm-period-btn ${period === "daily" ? "active" : ""}" data-dlm-period="daily" title="14 derniers jours">📅 Jour</button>
         <button class="dlm-period-btn ${period === "monthly" ? "active" : ""}" data-dlm-period="monthly" title="12 mois (N vs N-1)">📆 Mois</button>`;

    const banner = data.isCalibrated
      ? `<div class="dlm-banner info">
           <span>ℹ️</span>
           <span>Profil horaire/saisonnier calibré sur vos totaux ENGIE réels (${isGaz ? `${fmtInt(data.gaz.yearKwh)} kWh N / ${fmtInt(data.gaz.yearPrevKwh)} kWh N-1` : `${fmtInt(data.elec.yearKwh)} kWh N / ${fmtInt(data.elec.yearPrevKwh)} kWh N-1`}).</span>
         </div>`
      : "";

    const zlinkyStrip = !isGaz && data.zlinkyActive ? renderZLinkyTelemetryStrip(data.elec) : "";

    let bodyHTML = "";
    if (activeTab === "linky") {
      const e = data.elec;
      bodyHTML = `
        <div class="dlm-kpi-grid cols-4">
          <div class="dlm-kpi-card">
            <div class="dlm-kpi-top">
              <div>
                <div class="dlm-kpi-label">${e.powerIsLive ? "PUISSANCE (RÉELLE)" : "PUISSANCE (ESTIMÉE)"}</div>
                <div class="dlm-kpi-val" style="color:#34d399;">${fmtInt(e.powerVA)}<span class="unit">VA</span></div>
              </div>
              <div class="dlm-icon-box" style="background:rgba(16,185,129,0.14);color:#34d399;">⚡</div>
            </div>
            ${renderLivePowerGaugeSVG(e.powerVA, e.powerPct, e.isHcNow, e.ptecLabel)}
          </div>
          <div class="dlm-kpi-card">
            <div class="dlm-kpi-top">
              <div>
                <div class="dlm-kpi-label">CONSO. AUJOURD'HUI</div>
                <div class="dlm-kpi-val">${fmt(e.todayKwh, 1)}<span class="unit">kWh</span></div>
              </div>
              <div class="dlm-icon-box" style="background:rgba(250,204,21,0.14);color:#facc15;">⚡</div>
            </div>
            <div class="dlm-kpi-foot">Dont ${fmt(e.todayHcKwh, 1)} kWh en Heures Creuses</div>
          </div>
          <div class="dlm-kpi-card">
            <div class="dlm-kpi-top">
              <div>
                <div class="dlm-kpi-label">COÛT ESTIMÉ (24H)</div>
                <div class="dlm-kpi-val">${fmt(e.todayCost, 2)}<span class="unit">€</span></div>
              </div>
              <div class="dlm-icon-box" style="background:rgba(59,130,246,0.14);color:#60a5fa;">€</div>
            </div>
            <div class="dlm-kpi-foot">Moyenne ${fmt(e.avgHourlyCost, 3)} € / heure</div>
          </div>
          <div class="dlm-kpi-card">
            <div class="dlm-kpi-top">
              <div>
                <div class="dlm-kpi-label">CUMUL MOIS</div>
                <div class="dlm-kpi-val">${fmtInt(e.monthKwh)}<span class="unit">kWh</span></div>
              </div>
              <div class="dlm-icon-box" style="background:rgba(168,85,247,0.14);color:#c084fc;">📈</div>
            </div>
            <div class="dlm-kpi-foot">Prévision fin de mois : ${fmtInt(e.forecastMonth)} kWh</div>
          </div>
        </div>
        <div class="dlm-chart-card">
          <div class="dlm-chart-title">Courbe de Charge (24h)</div>
          <div class="dlm-chart-sub">Puissance horaire kWh (Bleu HC / Gris HP) et coût estimé (€)</div>
          ${renderLoadCurve24hSVG(e.hourlyPoints)}
        </div>
      `;
    } else if (activeTab === "elec") {
      const e = data.elec;
      const chartPoints = period === "monthly" ? e.monthly12 : e.daily14;
      const chartSvg = period === "monthly"
        ? renderComparativeBarChartSVG(chartPoints, "#3b82f6", "#8b5cf6", String(data.currentYear), String(data.prevYear))
        : renderComparativeBarChartSVG(chartPoints, "#3b82f6", "#475569", "Conso Actuelle", "Année N-1");

      const tempoColorDot = (c) => {
        if (!c) return "#64748b";
        if (c.includes("BLEU")) return "#3b82f6";
        if (c.includes("BLANC")) return "#f8fafc";
        if (c.includes("ROUGE")) return "#ef4444";
        return "#94a3b8";
      };

      const footerTempo = (e.tempoToday || e.tempoTomorrow)
        ? `<div class="dlm-tempo-card">
             <div class="dlm-tempo-head">
               <span class="dlm-kpi-label">EDF TEMPO / ZLINKY_TIC</span>
               <span class="dlm-tempo-badge">ACTIF (${e.maxKva} KVA)</span>
             </div>
             <div class="dlm-tempo-cols">
               <div>
                 <div class="dlm-tempo-day">AUJOURD'HUI</div>
                 <div class="dlm-tempo-val"><span class="dlm-dot" style="background:${tempoColorDot(e.tempoToday)}"></span>${esc(e.tempoToday || "—")}</div>
               </div>
               <div>
                 <div class="dlm-tempo-day">DEMAIN</div>
                 <div class="dlm-tempo-val"><span class="dlm-dot" style="background:${tempoColorDot(e.tempoTomorrow)}"></span>${esc(e.tempoTomorrow || "—")}</div>
               </div>
             </div>
           </div>`
        : `<div class="dlm-tempo-card">
             <div class="dlm-tempo-head">
               <span class="dlm-kpi-label">CONTRAT & TARIFICATION ENGIE</span>
               <span class="dlm-tempo-badge">${e.maxKva} KVA</span>
             </div>
             <div class="dlm-tempo-cols">
               <div>
                 <div class="dlm-tempo-day">OPTION TARIFAIRE</div>
                 <div class="dlm-tempo-val">${esc(e.tarifType)}</div>
               </div>
               <div>
                 <div class="dlm-tempo-day">CUMUL ANNUEL N / N-1</div>
                 <div class="dlm-tempo-val">${fmtInt(e.yearKwh)} / ${fmtInt(e.yearPrevKwh)} kWh</div>
               </div>
             </div>
           </div>`;

      bodyHTML = `
        <div class="dlm-kpi-grid cols-3">
          <div class="dlm-kpi-card">
            <div class="dlm-kpi-top">
              <div>
                <div class="dlm-kpi-label">${e.todayIsLive ? "CONSO. AUJOURD'HUI (LIVE)" : "CONSO. AUJOURD'HUI"}</div>
                <div class="dlm-kpi-val">${fmt(e.todayKwh, 1)}<span class="unit">kWh</span></div>
              </div>
              <div class="dlm-icon-box" style="background:rgba(59,130,246,0.14);color:#60a5fa;">⚡</div>
            </div>
            <div class="dlm-kpi-foot">Dont ${fmt(e.todayHcKwh, 1)} kWh en Heures Creuses</div>
          </div>
          <div class="dlm-kpi-card">
            <div class="dlm-kpi-top">
              <div>
                <div class="dlm-kpi-label">COÛT ESTIMÉ (24H)</div>
                <div class="dlm-kpi-val">${fmt(e.todayCost, 2)}<span class="unit">€</span></div>
              </div>
              <div class="dlm-icon-box" style="background:rgba(59,130,246,0.14);color:#60a5fa;">€</div>
            </div>
            <div class="dlm-kpi-foot">Moyenne ${fmt(e.avgHourlyCost, 3)} € / heure</div>
          </div>
          <div class="dlm-kpi-card">
            <div class="dlm-kpi-top">
              <div>
                <div class="dlm-kpi-label">CUMUL MOIS</div>
                <div class="dlm-kpi-val">${fmtInt(e.monthKwh)}<span class="unit">kWh</span></div>
              </div>
              <div class="dlm-icon-box" style="background:rgba(168,85,247,0.14);color:#c084fc;">📈</div>
            </div>
            <div class="dlm-kpi-foot">Prévision fin de mois : ${fmtInt(e.forecastMonth)} kWh</div>
          </div>
        </div>
        <div class="dlm-chart-card">
          <div class="dlm-chart-title">Historique Consommation</div>
          <div class="dlm-chart-sub">${period === "monthly" ? "Comparaison mensuelle ENGIE / Linky (Année N vs N-1)" : "Comparaison des relevés quotidiens (14 derniers jours)"}</div>
          ${chartSvg}
        </div>
        ${footerTempo}
      `;
    } else {
      const g = data.gaz;
      const chartPoints = period === "monthly" ? g.monthly12 : g.daily14;
      const chartSvg = period === "monthly"
        ? renderComparativeBarChartSVG(chartPoints, "#f97316", "#7c2d12", String(data.currentYear), String(data.prevYear))
        : renderComparativeBarChartSVG(chartPoints, "#f97316", "#475569", "Conso Gaz", "Année N-1");

      bodyHTML = `
        <div class="dlm-kpi-grid cols-4">
          <div class="dlm-kpi-card" style="align-items:center;justify-content:center;">
            ${renderGasRingGaugeSVG(g.lastDayKwh)}
          </div>
          <div class="dlm-kpi-card">
            <div class="dlm-kpi-top">
              <div>
                <div class="dlm-kpi-label">DERNIER RELEVÉ</div>
                <div class="dlm-kpi-val">${fmt(g.lastDayKwh, 1)}<span class="unit">kWh</span></div>
              </div>
              <div class="dlm-icon-box" style="background:rgba(249,115,22,0.14);color:#fb923c;">🔥</div>
            </div>
            <div class="dlm-kpi-foot">Le ${esc(g.lastDate)} · Cumul mois ${fmtInt(g.monthKwh)} kWh</div>
          </div>
          <div class="dlm-kpi-card">
            <div class="dlm-kpi-top">
              <div>
                <div class="dlm-kpi-label">COÛT ESTIMÉ (MOIS)</div>
                <div class="dlm-kpi-val">${fmtInt(g.monthEuro)}<span class="unit">€</span></div>
              </div>
              <div class="dlm-icon-box" style="background:rgba(239,68,68,0.14);color:#f87171;">€</div>
            </div>
            <div class="dlm-kpi-foot">~${fmt(g.avgPrice, 2)} € / kWh</div>
          </div>
          <div class="dlm-kpi-card">
            <div class="dlm-kpi-top">
              <div>
                <div class="dlm-kpi-label">VOLUME M³</div>
                <div class="dlm-kpi-val">${fmt(g.m3, 1)}<span class="unit">m³</span></div>
              </div>
              <div class="dlm-icon-box" style="background:rgba(148,163,184,0.14);color:#cbd5e1;">⏲</div>
            </div>
            <div class="dlm-kpi-foot">Coeff conversion: ${g.coeff}</div>
          </div>
        </div>
        <div class="dlm-chart-card">
          <div class="dlm-chart-title">Historique Consommation Gaz</div>
          <div class="dlm-chart-sub">${period === "monthly" ? "Comparaison mensuelle Gazpar (Année N vs N-1)" : "Comparaison quotidienne Gazpar (14 derniers jours)"}</div>
          ${chartSvg}
        </div>
      `;
    }

    return `
      <style>${MONITEUR_CSS}</style>
      <div class="dlm-root">
        <div class="dlm-header">
          <div class="dlm-title-row">
            <span class="dlm-title-icon">${headerIcon}</span>
            <div>
              <h2 class="dlm-title">${esc(config.title || "Moniteur Énergie")}</h2>
              <div class="dlm-subtitle">
                <span class="dlm-dot" style="background:${subDotColor}"></span>
                <span>${subLabel}</span>
              </div>
            </div>
          </div>
          <div class="dlm-controls">
            ${!state.lockTabs ? `
              <div class="dlm-pill-group">
                <button class="dlm-tab-btn ${activeTab === "linky" ? "active-linky" : ""}" data-dlm-tab="linky">⏲ LINKY</button>
                <button class="dlm-tab-btn ${activeTab === "elec" ? "active-elec" : ""}" data-dlm-tab="elec">⚡ ÉLEC</button>
                <button class="dlm-tab-btn ${activeTab === "gas" ? "active-gas" : ""}" data-dlm-tab="gas">🔥 GAZ</button>
              </div>
            ` : ""}
            <div class="dlm-pill-group">
              ${periodControls}
            </div>
          </div>
        </div>
        ${zlinkyStrip}
        ${banner}
        ${bodyHTML}
      </div>
    `;
  }

  window.DomoLinkEngieMoniteur = {
    CSS: MONITEUR_CSS,
    renderMoniteurHTML,
    extractAccountFromHass,
    autoDetectCompanionSensors,
  };

  class DomoLinkEngieCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._config = {};
      this._hass = null;
      this._snapshotAcc = null;
      this._lastWsFetch = 0;
      this._moniteurState = { tab: "linky", period: "daily", lockTabs: false };
    }

    setConfig(config) {
      this._config = {
        mode: "moniteur_full",
        title: "Moniteur Énergie",
        compte_contrat: "",
        default_tab: "linky",
        default_period: "daily",
        show_alerts: true,
        ...config,
      };
      if (this._config.mode === "moniteur_linky_24h") {
        this._moniteurState = { tab: "linky", period: "hourly", lockTabs: true };
      } else if (this._config.mode === "moniteur_elec") {
        this._moniteurState = { tab: "elec", period: this._config.default_period || "daily", lockTabs: true };
      } else if (this._config.mode === "moniteur_gaz") {
        this._moniteurState = { tab: "gas", period: this._config.default_period || "daily", lockTabs: true };
      } else {
        this._moniteurState = {
          tab: this._config.default_tab || "linky",
          period: this._config.default_period || "daily",
          lockTabs: false,
        };
      }
      this._render();
    }

    set hass(hass) {
      this._hass = hass;
      const now = Date.now();
      if (hass && hass.callWS && now - this._lastWsFetch > 60000) {
        this._lastWsFetch = now;
        hass
          .callWS({ type: "domolink_engie/get_snapshot" })
          .then((res) => {
            const accs = (res && res.accounts) || [];
            const cc = this._config.compte_contrat;
            this._snapshotAcc = (cc && accs.find((a) => a.compte_contrat === cc)) || accs[0] || null;
            this._render();
          })
          .catch(() => {});
      }
      this._render();
    }

    getCardSize() {
      if (this._config.mode === "compact") return 3;
      if (String(this._config.mode).startsWith("moniteur_")) return 7;
      return 6;
    }

    static getConfigElement() {
      return document.createElement("domolink-engie-card-editor");
    }

    static getStubConfig() {
      return {
        mode: "moniteur_full",
        default_tab: "linky",
        default_period: "daily",
      };
    }

    _getState(slugs) {
      if (!this._hass || !this._hass.states) return null;
      const cc = this._config.compte_contrat || this._detectCC();
      if (!cc) return null;
      const list = Array.isArray(slugs) ? slugs : [slugs];
      const prefixes = [
        `sensor.engie_electricite_${cc}_`,
        `sensor.engie_gaz_${cc}_`,
        `sensor.engie_compte_${cc}_`,
        `sensor.engie_erl_linky_${cc}_`,
        `sensor.engie_${cc}_`,
      ];
      for (const s of list) {
        for (const p of prefixes) {
          if (this._hass.states[p + s]) return this._hass.states[p + s];
        }
      }
      return null;
    }

    _detectCC() {
      if (this._snapshotAcc && this._snapshotAcc.compte_contrat) return this._snapshotAcc.compte_contrat;
      if (!this._hass || !this._hass.states) return "";
      const m = Object.keys(this._hass.states).find((k) =>
        /^sensor\.engie_(?:electricite_|gaz_|compte_|erl_linky_)?(\d+)_/.test(k)
      );
      return m ? m.match(/^sensor\.engie_(?:electricite_|gaz_|compte_|erl_linky_)?(\d+)_/)[1] : "";
    }

    _render() {
      if (!this.shadowRoot) return;
      const mode = this._config.mode || "moniteur_full";

      if (mode.startsWith("moniteur_")) {
        const account = extractAccountFromHass(this._hass, this._config, this._snapshotAcc);
        this.shadowRoot.innerHTML = `<ha-card style="background:transparent;border:0;box-shadow:none;">${renderMoniteurHTML(account, this._hass, this._moniteurState, this._config, () => this._render())}</ha-card>`;
        this.shadowRoot.querySelectorAll("[data-dlm-tab]").forEach((btn) => {
          btn.addEventListener("click", () => {
            this._moniteurState.tab = btn.dataset.dlmTab;
            this._render();
          });
        });
        this.shadowRoot.querySelectorAll("[data-dlm-period]").forEach((btn) => {
          btn.addEventListener("click", () => {
            this._moniteurState.period = btn.dataset.dlmPeriod;
            this._render();
          });
        });
        return;
      }

      // Legacy v1.1.0 modes (energy_full, compact, billing) preserved with multi-slug compatibility
      const eKwh = this._getState(["consommation_electricite_mois", "elec_monthly_conso", "electricite_mois_kwh"]);
      const eEuro = this._getState(["cout_electricite_mois", "elec_monthly_cost", "electricite_mois_euro"]);
      const gKwh = this._getState(["consommation_gaz_mois_kwh", "gaz_monthly_kwh", "gaz_mois_kwh"]);
      const gEuro = this._getState(["cout_gaz_mois", "gaz_monthly_cost", "gaz_mois_euro"]);
      const solde = this._getState(["solde_a_payer", "account_solde"]);
      const mens = this._getState(["mensualite_globale", "account_mensualite", "mensualite"]);

      const val = (st, unit = "") => (st && st.state !== "unknown" && st.state !== "unavailable" ? `${st.state}${unit ? " " + unit : ""}` : "—");

      this.shadowRoot.innerHTML = `
        <style>
          ha-card { background: var(--ha-card-background, #0f172a); color: #f8fafc; border-radius: 16px; padding: 16px; border: 1px solid rgba(148,163,184,.16); }
          .head { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; font-weight:700; font-size:15px; }
          .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; }
          .tile { background:rgba(30,41,59,.7); border:1px solid rgba(148,163,184,.12); border-radius:12px; padding:12px; }
          .lbl { font-size:11px; color:#94a3b8; text-transform:uppercase; font-weight:700; }
          .v { font-size:20px; font-weight:800; margin-top:4px; }
        </style>
        <ha-card>
          <div class="head"><span>⚡ ${esc(this._config.title || "DomoLink ENGIE")}</span><span style="font-size:11px;color:#60a5fa;">${esc(mode)}</span></div>
          <div class="grid">
            ${mode !== "billing" ? `
              <div class="tile"><div class="lbl">⚡ Élec (mois)</div><div class="v">${esc(val(eKwh, "kWh"))}</div><div class="lbl" style="margin-top:4px;">${esc(val(eEuro, "€"))}</div></div>
              <div class="tile"><div class="lbl">🔥 Gaz (mois)</div><div class="v">${esc(val(gKwh, "kWh"))}</div><div class="lbl" style="margin-top:4px;">${esc(val(gEuro, "€"))}</div></div>
            ` : ""}
            ${mode !== "compact" ? `
              <div class="tile"><div class="lbl">💶 Mensualité</div><div class="v">${esc(val(mens, "€"))}</div></div>
              <div class="tile"><div class="lbl">🧾 Solde</div><div class="v">${esc(val(solde, "€"))}</div></div>
            ` : ""}
          </div>
        </ha-card>
      `;
    }
  }

  class DomoLinkEngieCardEditor extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._config = {};
    }

    setConfig(config) {
      this._config = { ...config };
      this._render();
    }

    _emit(key, val) {
      this._config = { ...this._config, [key]: val };
      this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
    }

    _render() {
      if (!this.shadowRoot) return;
      const c = this._config;
      this.shadowRoot.innerHTML = `
        <style>
          .ed { display:flex; flex-direction:column; gap:12px; padding:8px 0; font-family:inherit; }
          label { display:flex; flex-direction:column; gap:4px; font-size:12px; font-weight:600; }
          select, input { padding:8px 10px; border-radius:8px; border:1px solid var(--divider-color, #475569); background:var(--card-background-color, #1e293b); color:var(--primary-text-color, #f8fafc); }
        </style>
        <div class="ed">
          <label>Mode d'affichage
            <select id="ed-mode">
              <option value="moniteur_full" ${c.mode === "moniteur_full" ? "selected" : ""}>Moniteur Énergie Complet (DomoLink26 — LINKY + ÉLEC + GAZ)</option>
              <option value="moniteur_linky_24h" ${c.mode === "moniteur_linky_24h" ? "selected" : ""}>Moniteur LINKY 24h (Jauge + Courbe de Charge)</option>
              <option value="moniteur_elec" ${c.mode === "moniteur_elec" ? "selected" : ""}>Moniteur ÉLEC (Jour / Mois + EDF Tempo)</option>
              <option value="moniteur_gaz" ${c.mode === "moniteur_gaz" ? "selected" : ""}>Moniteur GAZ (Jauge Gazpar + Jour / Mois)</option>
              <option value="energy_full" ${c.mode === "energy_full" ? "selected" : ""}>Vue Classique Complète</option>
              <option value="compact" ${c.mode === "compact" ? "selected" : ""}>Vue Compacte</option>
              <option value="billing" ${c.mode === "billing" ? "selected" : ""}>Vue Facturation & Mensualités</option>
            </select>
          </label>
          <label>Onglet initial (mode complet)
            <select id="ed-tab">
              <option value="linky" ${c.default_tab === "linky" ? "selected" : ""}>LINKY (24h)</option>
              <option value="elec" ${c.default_tab === "elec" ? "selected" : ""}>ÉLEC</option>
              <option value="gas" ${c.default_tab === "gas" ? "selected" : ""}>GAZ</option>
            </select>
          </label>
          <label>Période par défaut (ÉLEC / GAZ)
            <select id="ed-period">
              <option value="daily" ${c.default_period === "daily" ? "selected" : ""}>Jour (14 derniers jours)</option>
              <option value="monthly" ${c.default_period === "monthly" ? "selected" : ""}>Mois (Jan–Déc N vs N-1)</option>
            </select>
          </label>
          <label>Préfixe appareil ZLinky_TIC optionnel (ex: zlinky_tic)
            <input id="ed-zlinky-prefix" type="text" value="${esc(c.zlinky_device_prefix || "")}" placeholder="Auto-détection Z2M / ZHA / deCONZ si vide" />
          </label>
          <label>Capteur puissance VA optionnel (ex: sensor.zlinky_tic_sinsts)
            <input id="ed-power" type="text" value="${esc(c.power_sensor || "")}" placeholder="Auto-détection si vide" />
          </label>
          <label>Capteur énergie jour optionnel (ex: sensor.compteur_linky_energie_quotidienne)
            <input id="ed-daily" type="text" value="${esc(c.daily_energy_sensor || "")}" placeholder="Auto-détection / calcul via Index si vide" />
          </label>
        </div>
      `;
      this.shadowRoot.getElementById("ed-mode").addEventListener("change", (e) => this._emit("mode", e.target.value));
      this.shadowRoot.getElementById("ed-tab").addEventListener("change", (e) => this._emit("default_tab", e.target.value));
      this.shadowRoot.getElementById("ed-period").addEventListener("change", (e) => this._emit("default_period", e.target.value));
      this.shadowRoot.getElementById("ed-zlinky-prefix").addEventListener("change", (e) => this._emit("zlinky_device_prefix", e.target.value.trim()));
      this.shadowRoot.getElementById("ed-power").addEventListener("change", (e) => this._emit("power_sensor", e.target.value.trim()));
      this.shadowRoot.getElementById("ed-daily").addEventListener("change", (e) => this._emit("daily_energy_sensor", e.target.value.trim()));
    }
  }

  if (!customElements.get("domolink-engie-card")) {
    customElements.define("domolink-engie-card", DomoLinkEngieCard);
  }
  if (!customElements.get("domolink-engie-card-editor")) {
    customElements.define("domolink-engie-card-editor", DomoLinkEngieCardEditor);
  }

  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c.type === "domolink-engie-card")) {
    window.customCards.push({
      type: "domolink-engie-card",
      name: "DomoLink ENGIE — Moniteur Énergie",
      description: "Vues Moniteur Énergie DomoLink26 (LINKY 24h + ZLinky_TIC Temps Réel, ÉLEC Jour/Mois, GAZ Gazpar) + Facturation ENGIE.",
      preview: true,
    });
  }
})();
