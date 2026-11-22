// Long-distance meeting planner: exact DP over trips, tracking both leave budgets.
(function (root) {
  const DAY = 86400000;
  function ymd(d) { return d.toISOString().slice(0, 10); }
  function parseD(s) { return new Date(s + "T00:00:00Z"); }

  // All fare data is generated from config/2027.toml + data/fares.csv by `meetplan build-web` (see meetplan/web.py):
  // curves = lowest fare of the latest survey per segment, QUOTES = every observation, CITIES = third-city options.
  const DATA = /*__DATA__*/null;
  const CURVE = DATA.curve;   // { me: [[MM-DD, €], ...] Paris→CDMX, her: [...] CDMX→Paris }
  const QUOTES = DATA.quotes; // evidence table rows

  // Price uncertainty. The curves are the LOWEST fare seen. A ticket actually bought is modelled as
  // low × m, m ~ Triangular(a, c, b) (from the TOML band).
  const BAND = DATA.band;
  const triMean = (BAND.a + BAND.b + BAND.c) / 3;
  function triQuantile(u) {
    const { a, b, c } = BAND, f = (c - a) / (b - a);
    return u < f ? a + Math.sqrt(u * (b - a) * (c - a)) : b - Math.sqrt((1 - u) * (b - a) * (b - c));
  }
  const BASIS = { low: 1, typical: triMean, cautious: triQuantile(0.9) };
  const basisF = p => BASIS[p.fareBasis || "typical"] || 1;

  // Third-city meetups: ref = normalised midweek fare from each origin at REF_DAY; seen = lowest fare observed in a
  // departure month (overrides the seasonal estimate); q = the quotes, for display.
  const CITIES = DATA.cities;
  const REF_DAY = DATA.refDay;

  function buildCalendar(p) {
    const y = p.year, start = Date.UTC(y, 0, 1);
    const N = (Date.UTC(y + 1, 0, 1) - start) / DAY;
    const days = [];
    const hFR = new Set(p.holidaysMe), hMX = new Set(p.holidaysHer);
    const blackHer = new Set(p.blackoutHerExtra || []);
    for (let i = 0; i < N; i++) {
      const d = new Date(start + i * DAY), iso = ymd(d), dow = d.getUTCDay();
      const wk = dow === 0 || dow === 6;
      const m = d.getUTCMonth();
      days.push({ i, iso, dow, m, wk, workMe: !wk && !hFR.has(iso), workHer: !wk && !hMX.has(iso),
        herBlocked: (p.blockAugust && m === 7) || blackHer.has(iso), turn: false, holMe: hFR.has(iso), holHer: hMX.has(iso) });
    }
    // Month turn: she can't take leave on her last `lastWD` working days of a month nor her first `firstWD` of the
    // next one. Counted in HER working days, so weekends and Mexican holidays are skipped over.
    for (let m = 0; m < 12; m++) {
      const md = days.filter(d => d.m === m && d.workHer);
      const mark = d => { d.herBlocked = true; d.turn = true; };
      md.slice(Math.max(0, md.length - (p.lastWD ?? 1))).forEach(mark);
      md.slice(0, p.firstWD ?? 3).forEach(mark);
    }
    // price curves per departure day
    const curve = {};
    for (const dir of ["me", "her"]) {
      const raw = days.map(d => {
        let v = CURVE[dir][0][1];
        for (const [md, pr] of CURVE[dir]) if (d.iso.slice(5) >= md) v = pr;
        return v;
      });
      // 5-day moving average for smooth transitions
      curve[dir] = raw.map((_, i) => {
        let s = 0, n = 0;
        for (let k = -2; k <= 2; k++) if (raw[i + k] != null) { s += raw[i + k]; n++; }
        return s / n;
      });
    }
    const ri = days.findIndex(d => d.iso.slice(5) === REF_DAY);
    const refCurve = { me: curve.me[ri], her: curve.her[ri] };
    return { N, days, curve, refCurve };
  }

  function tripPrice(cal, p, s, e, dir) {
    const c = cal.curve[dir];
    const dowS = cal.days[s].dow, dowE = cal.days[e].dow;
    let f = 1;
    if (dowS === 5 || dowS === 6) f += DATA.weekend;   // weekend departures cost a bit more
    if (dowE === 0) f += DATA.sunday;           // Sunday returns too
    return p.priceScale * basisF(p) * (0.6 * c[s] + 0.4 * c[e]) * f;
  }

  function cityPrice(cal, p, s, e, city, who) {
    const c = cal.curve[who], ref = cal.refCurve[who];
    const dowS = cal.days[s].dow;
    let f = 1; if (dowS === 5 || dowS === 6) f += DATA.weekend;
    // A lowest fare actually observed for this city in the departure month beats the seasonal estimate.
    const seen = city.seen && city.seen[cal.days[s].m + 1];
    if (seen && seen[who] != null) return p.priceScale * basisF(p) * seen[who] * f;
    return p.priceScale * basisF(p) * city.ref[who] * ((0.6 * c[s] + 0.4 * c[e]) / ref) * f;
  }

  // Evaluate one trip option. dir: "me" (he flies to Mexico) | "her" (she flies to Paris). hostLeave: host takes leave.
  function tripStats(cal, p, s, e, dir, hostLeave) {
    let la = 0, lg = 0, value = 0, ok = true;
    if (dir.startsWith("meet:")) {
      const city = p._cityMap[dir.slice(5)];
      const bonus = (p.cityBonus && p.cityBonus[city.id] != null) ? p.cityBonus[city.id] : 0;
      for (let i = s; i <= e; i++) {
        const d = cal.days[i], edge = (i === s || i === e) ? p.travelDayValue : 1;
        if (d.workMe) la++;
        if (d.workHer) { lg++; if (d.herBlocked) ok = false; }
        value += (p.V + bonus) * edge;
      }
      const priceMe = cityPrice(cal, p, s, e, city, "me"), priceHer = cityPrice(cal, p, s, e, city, "her");
      const lodging = (p.lodging && p.lodging[city.id] != null ? p.lodging[city.id] : city.lodging) * (e - s);
      const price = priceMe + priceHer;
      // Meeting halfway is an effort we choose to make: its cost counts at meetCostFactor in the objective.
      const wc = (p.costWeight ?? 1) * (p.meetCostFactor ?? 1);
      return { ok, la, lg, value, price, priceMe, priceHer, lodging, city: city.id,
        net: value + (p.tripBonus || 0) - wc * (price + lodging + p.C) };
    }
    for (let i = s; i <= e; i++) {
      const d = cal.days[i];
      const edge = (i === s || i === e) ? p.travelDayValue : 1; // travel days count partially
      if (dir === "me") {
        if (d.workMe) la++;
        let hostWorking = d.workHer && !hostLeave;
        if (d.workHer && hostLeave) { lg++; if (d.herBlocked) ok = false; }
        value += p.V * edge * (hostWorking ? p.beta : 1);
      } else {
        if (d.workHer) { lg++; if (d.herBlocked) ok = false; }
        let hostWorking = d.workMe && !hostLeave;
        if (d.workMe && hostLeave) la++;
        value += p.V * edge * (hostWorking ? p.beta : 1);
      }
    }
    const price = tripPrice(cal, p, s, e, dir);
    return { ok, la, lg, value, price, priceMe: dir === "me" ? price : 0, priceHer: dir === "her" ? price : 0, lodging: 0, city: null,
      net: value + (p.tripBonus || 0) - (p.costWeight ?? 1) * (price + p.C) };
  }

  function solve(p) {
    const cal = buildCalendar(p);
    const N = cal.N;
    const idx = iso => Math.round((parseD(iso) - Date.UTC(p.year, 0, 1)) / DAY);
    const reqList = p.required.slice();
    // "Make sure we meet in month X": one extra required trip that starts in that month (free-trip length rules).
    if (p.focusMonth != null && p.focusMonth >= 0) {
      const m = p.focusMonth, y = p.year;
      const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
      reqList.push({ label: "focus", focus: true, sMin: ymd(new Date(Date.UTC(y, m, 1))), sMax: ymd(new Date(Date.UTC(y, m, last - 6))),
        eMin: ymd(new Date(Date.UTC(y, m, 7))), eMax: ymd(new Date(Date.UTC(y, m + 1, 7))),
        dir: p.focusWhere || (p.mode === "home" ? "any" : p.mode === "meet" ? "meet:any" : "anywhere"), minDays: p.Lmin, maxDays: p.Lmax, focusM: m });
    }
    const reqs = reqList.map(r => ({
      ...r, sMin: idx(r.sMin), sMax: idx(r.sMax), eMin: idx(r.eMin), eMax: idx(r.eMax)
    })).sort((a, b) => a.sMin - b.sMin);
    const R = reqs.length;

    // Pre-compute all trip options: per start s, list of {e, dir, hl, la, lg, net, req}
    p._cityMap = Object.fromEntries(CITIES.map(c => [c.id, c]));
    const enabled = CITIES.filter(c => !p.citiesOff || !p.citiesOff.includes(c.id)).map(c => "meet:" + c.id);
    const opts = Array.from({ length: N }, () => []);
    const homeDirs = ["me", "her"];
    const freeDirs = p.mode === "home" ? homeDirs : p.mode === "meet" ? enabled : homeDirs.concat(enabled);
    const dirsFor = d => d === "any" ? homeDirs : d === "meet:any" ? enabled : d === "anywhere" ? homeDirs.concat(enabled) : [d];
    for (let s = 0; s < N; s++) {
      for (let L = p.Lmin; L <= p.Lmax; L++) {
        const e = s + L - 1; if (e >= N) break;
        for (const dir of freeDirs) for (const hl of (dir.startsWith("meet:") ? [false] : [false, true])) {
          const t = tripStats(cal, p, s, e, dir, hl);
          if (t.ok) opts[s].push({ s, e, dir, hl, ...t, req: -1 });
        }
      }
    }
    reqs.forEach((r, k) => {
      for (let s = r.sMin; s <= r.sMax; s++) for (let e = Math.max(r.eMin, s); e <= r.eMax; e++) {
        if (r.minDays && e - s + 1 < r.minDays) continue;
        if (r.maxDays && e - s + 1 > r.maxDays) continue;
        if (r.focusM != null) { let k = 0; for (let i = s; i <= e; i++) if (cal.days[i].m === r.focusM) k++; if (2 * k < e - s + 1) continue; }
        for (const dir of dirsFor(r.dir)) for (const hl of (dir.startsWith("meet:") ? [false] : [false, true])) {
          const t = tripStats(cal, p, s, e, dir, hl);
          if (t.ok) opts[s].push({ s, e, dir, hl, ...t, req: k });
        }
      }
    });

    const pen = g => p.lambda * Math.max(0, g - p.gapTarget) ** 2;
    const A1 = p.leaveMe + 1, G1 = p.leaveHer + 1, S = A1 * G1, RS = (R + 1) * S;
    const NEG = -1e30;
    // flatten all options
    const all = []; for (let s = 0; s < N; s++) for (const o of opts[s]) all.push(o);
    const V = new Float32Array((N + 1) * RS).fill(NEG);
    const Vch = new Int16Array((N + 1) * RS).fill(-1);
    const B = new Float32Array(N * RS).fill(NEG);
    const Bch = new Int32Array(N * RS).fill(-1);
    const oIdx = new Map(); all.forEach((o, k) => oIdx.set(o, k));
    const rLo = r => (r === 0 ? 0 : reqs[r - 1].eMin + 1);
    const rHi = r => (r === R ? N : reqs[r].sMax);
    for (let t = N; t >= 0; t--) {
      if (t < N) {
        for (const o of opts[t]) {
          const k = oIdx.get(o), nt = o.e + 1;
          for (let r = 0; r <= R; r++) {
            if (t < rLo(r) || t > rHi(r)) continue;
            let nr;
            if (o.req >= 0) { if (o.req !== r) continue; nr = r + 1; }
            else { if (r < R && o.e + 1 + p.minGap > reqs[r].sMax) continue; nr = r; }
            const vb = (nt * (R + 1) + nr) * S, bb = (t * (R + 1) + r) * S;
            for (let a = o.la; a < A1; a++) {
              const rowV = vb + (a - o.la) * G1 - o.lg, rowB = bb + a * G1;
              for (let g = o.lg; g < G1; g++) {
                const v = o.net + V[rowV + g];
                if (v > B[rowB + g]) { B[rowB + g] = v; Bch[rowB + g] = k; }
              }
            }
          }
        }
      }
      for (let r = 0; r <= R; r++) {
        if (t < rLo(r) - 1 || t > rHi(r)) continue;
        const base = (t * (R + 1) + r) * S;
        const endV = r === R ? -pen(N - t) : NEG;
        for (let x = 0; x < S; x++) V[base + x] = endV;
        const s0 = t === 0 ? 0 : t + p.minGap, s1 = Math.min(N - 1, t + p.maxGap);
        for (let s = s0; s <= s1; s++) {
          const pg = pen(s - t), bb = (s * (R + 1) + r) * S;
          for (let x = 0; x < S; x++) {
            const v = B[bb + x] - pg;
            if (v > V[base + x]) { V[base + x] = v; Vch[base + x] = s - t; }
          }
        }
      }
    }
    let best = null;
    const root0 = (p.leaveMe) * G1 + p.leaveHer;
    if (V[root0] > NEG / 2) {
      const trips = []; let t = 0, r = 0, a = p.leaveMe, g = p.leaveHer, guard = 0;
      while (guard++ < 200 && t < N) {
        const off = Vch[(t * (R + 1) + r) * S + a * G1 + g]; if (off < 0) break;
        const s = t + off, o = all[Bch[(s * (R + 1) + r) * S + a * G1 + g]];
        trips.push(o); if (o.req >= 0) r++; a -= o.la; g -= o.lg; t = o.e + 1;
      }
      let la = 0, lg = 0, price = 0, days = 0, prev = 0, maxGap = 0, gapPen = 0, net = 0, lodging = 0, payMe = 0, payHer = 0;
      for (const o of trips) {
        la += o.la; lg += o.lg; price += o.price; lodging += o.lodging; payMe += o.priceMe; payHer += o.priceHer; days += o.e - o.s + 1; net += o.net;
        const gg = o.s - prev; maxGap = Math.max(maxGap, gg); gapPen += pen(gg); prev = o.e + 1;
      }
      maxGap = Math.max(maxGap, N - prev); gapPen += pen(N - prev);
      best = { trips, ev: { la, lg, price, lodging, payMe, payHer, days, maxGap, gapPen, objective: net - gapPen } };
    }
    // price comparison per free window: cheapest way to fill each month
    return { cal, reqs, best, quotes: QUOTES };
  }

  // Monte Carlo: redraw every ticket of a plan from the band; returns P10/P50/P90 of total travel cost (fares + hotels).
  function simulate(p, trips, n = 4000, seed = 12345) {
    let st = seed >>> 0;
    const rnd = () => { st = (st * 1664525 + 1013904223) >>> 0; return st / 4294967296; };
    const tickets = [];
    let fixed = 0;
    for (const o of trips) {
      fixed += o.lodging;
      for (const v of [o.priceMe, o.priceHer]) if (v > 0) tickets.push(v / basisF(p));  // back to the 'lowest seen' base
    }
    const out = new Float64Array(n);
    for (let k = 0; k < n; k++) {
      let t = fixed;
      for (const base of tickets) t += base * triQuantile(rnd());
      out[k] = t;
    }
    out.sort();
    const q = x => out[Math.min(n - 1, Math.floor(x * n))];
    return { p10: q(0.1), p50: q(0.5), p90: q(0.9), min: out[0], max: out[n - 1], tickets: tickets.length };
  }

  const api = { simulate, BAND, BASIS, solve, buildCalendar, tripPrice, cityPrice, CURVE, QUOTES, CITIES, DATA };
  if (typeof module !== "undefined") module.exports = api; else root.Planner = api;
})(this);
