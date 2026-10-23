"""Positioning ablation: which sensors help place the train between stations without GPS.

Held-out landmark error: for an ordered pair (A held out, B reference) of clean runs over the same
section, curve edges found from yaw (position.curve_landmarks) are the truth. For landmark k, A's yaw
(and heading, which is re-integrated so it cannot carry the hidden turn: position.bridge_heading) is
masked within +-MASK of the section fraction around it, A is aligned to B with a feature set, and
error = |warp(a_k) - b_k| x section length (L0 fractions times the straight-line length; a check in
B's dead-reckoned distance fraction is also reported). Inside the mask the alignment can only use the
other channels plus continuity, so the test asks: does a sensor beat bridging the gap between the
neighbouring curve fixes? Baselines that use no DTW at all (interpolating between the other matched
curve edges, or L0 plus one offset fitted on them) show what that bridging alone achieves. Unmasked
errors are also reported (optimistic: for yaw sets the landmark is in the signal being matched).

CIs are bootstrap over physical curve edges (all rows of one edge resampled together), not rows.

Reads outputs/sections.json, outputs/qc.json, outputs/gonogo/repeatability.json (clean flags); fetches
the public TfL Route/Sequence for track lengths. Writes outputs/positioning/{track_lengths.json,
ablation.json, ablation.png, gap_length.png, alignment_example.png, landmarks.png, improvement_log.md}.
Run: .venv/Scripts/python scripts/ablation.py
"""
from __future__ import annotations

import itertools
import json
import time
import urllib.request

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from tubesense import features as F
from tubesense import position as P
from tubesense import repeat as R
from tubesense.ingest import ROOT, load_run
from tubesense.stops import pauses

OUT = ROOT / "outputs" / "positioning"
TRACKS = [("EB", "LST", "BTH"), ("EB", "BTH", "MLE"), ("WB", "BTH", "LST"), ("WB", "MLE", "BTH")]
STATIONS = {"LST": (51.517372, -0.083182), "BTH": (51.527222, -0.055506), "MLE": (51.525122, -0.03364)}
TFL = "https://api.tfl.gov.uk/Line/central/Route/Sequence/{}"
MASKS = (0.04, 0.08, 0.12, 0.16, 0.24)
HEAD = "m0.08"  # headline mask
HEADLINE = "yaw"  # headline L2 set: the one position-locked channel (not picked as the minimum over sets)
CURVE = ("yaw", "head")  # channels masked around the landmark
SLACK = 0.035  # open-end variant: +-0.035 of the section ~ +-5 s, the boundary accuracy
N_BOOT = 2000
STRESS = ["R4:BTH-LST", "R4:MLE-BTH"]  # excluded runs (pause / handheld / floor), stress test only

ALL = list(P.FEATURES)
FIXES = ("L0 + offset", "interp curve fixes", "interp curve fixes, open ends")
# name -> (channels or None for a baseline, open-end slack, leaky heading)
SPEC = {"L0 time fraction": (None, 0, False), "L0 + offset": (None, 0, False),
        "L1 dead reckoning": (None, 0, False),
        "interp curve fixes": (None, 0, False), "interp curve fixes, open ends": (None, 0, False),
        **{f: ([f], 0, False) for f in ALL},
        "yaw, open ends": (["yaw"], SLACK, False),
        **{f"yaw+{f}": (["yaw", f], 0, False) for f in ALL if f != "yaw"},
        "yaw+head, leaky heading": (["yaw", "head"], 0, True),
        "yaw+vib+drive": (["yaw", "vib", "drive"], 0, False),
        "yaw+vib+drive+mag": (["yaw", "vib", "drive", "mag"], 0, False),
        "all": (ALL, 0, False), "all, open ends": (ALL, SLACK, False),
        **{f"all-{f}": ([g for g in ALL if g != f], 0, False) for f in ALL}}
BASELINES = [n for n, s in SPEC.items() if s[0] is None]


def _haversine(a, b):
    la1, lo1, la2, lo2 = map(np.radians, (*a, *b))
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return float(2 * 6371008.8 * np.arcsin(np.sqrt(h)))


# --------------------------------------------------------------------------- 1. track lengths
def track_lengths() -> dict:
    """Along-track LST-BTH / BTH-MLE from TfL lineStrings if detailed, else the straight line."""
    straight = {"LST-BTH": _haversine(STATIONS["LST"], STATIONS["BTH"]),
                "BTH-MLE": _haversine(STATIONS["BTH"], STATIONS["MLE"])}
    out = {"straight_line_m": {k: round(v, 1) for k, v in straight.items()}, "tfl": {}, "fetched_utc": None}
    try:
        polys = []
        for d in ("outbound", "inbound"):
            req = urllib.request.Request(TFL.format(d), headers={"User-Agent": "tubesense-hackathon"})
            j = json.loads(urllib.request.urlopen(req, timeout=10).read())
            polys += [(d, np.array(p)) for ls in j["lineStrings"] for p in json.loads(ls)]
        out["fetched_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for k in straight:
            a, b = k.split("-")
            best = None
            for d, p in polys:  # p: (n, 2) lon, lat
                near = {s: int(np.argmin([_haversine(STATIONS[s], (q[1], q[0])) for q in p])) for s in (a, b)}
                off = max(_haversine(STATIONS[s], (p[near[s]][1], p[near[s]][0])) for s in (a, b))
                if off > 100:
                    continue
                i0, i1 = sorted(near.values())
                L = sum(_haversine((p[i][1], p[i][0]), (p[i + 1][1], p[i + 1][0])) for i in range(i0, i1))
                best = {"direction": d, "intermediate_vertices": i1 - i0 - 1, "polyline_m": round(L, 1)}
                break
            out["tfl"][k] = best
    except Exception as e:  # network or format: fall back loudly
        out["tfl_error"] = repr(e)
    detailed = {k: v for k, v in out["tfl"].items() if v and v["intermediate_vertices"] >= 3}
    out["used_m"] = {k: (detailed[k]["polyline_m"] if k in detailed else round(straight[k], 1)) for k in straight}
    out["source"] = {k: ("TfL Route/Sequence lineString" if k in detailed else "straight line between station coords")
                     for k in straight}
    if len(detailed) < len(straight):
        out["warning"] = ("TfL lineStrings for the Central line join consecutive stations with single straight "
                          "segments (0 intermediate vertices between LST, BTH and MLE), so they give the chord, "
                          "not the along-track length. FALLING BACK to straight-line lengths. True track is "
                          "longer by the arc/chord ratio (see yaw_arc_chord); errors in metres scale with it.")
    out["urls"] = [TFL.format(d) for d in ("outbound", "inbound")]
    return out


# --------------------------------------------------------------------------- 2. features + landmarks
def build():
    secs = json.load(open(ROOT / "outputs" / "sections.json"))
    qc = json.load(open(ROOT / "outputs" / "qc.json"))["runs"]
    meta = json.load(open(ROOT / "outputs" / "gonogo" / "repeatability.json"))["sections"]
    sig, lm, lm_h, dist, baro_only, cache = {}, {}, {}, {}, {}, {}
    for sec in secs["sections"]:
        if (sec["direction"], sec["from"], sec["to"]) not in TRACKS:
            continue
        stem = sec["stem"]
        if stem not in cache:
            run = load_run(stem)
            dw = next(r for r in secs["runs"] if r["stem"] == stem)["dwells"]
            yaw = F.yaw_rate(run, F.gyro_bias(run, [(d["t0"], d["t1"]) for d in dw]))
            mode = qc[stem]["magnetometer"]["mode"]
            mag = F.mag_destep(run)[0] if mode == "calibrated" else run.sensors["mag"]
            cache[stem] = (run, yaw, P.vib_series(run), mag, [(p["t"], p["dur"]) for p in pauses(run)])
        run, yaw, vib, mag, gaps = cache[stem]
        k = f"{sec['run_id']}:{sec['from']}-{sec['to']}"
        sig[k] = P.section_stack(run, sec, yaw, vib, mag)
        lm[k] = P.curve_landmarks(run, sec, yaw)
        lm_h[k] = P.curve_landmarks(run, sec, yaw, edge="heading")
        dr = F.dead_reckon(run, sec["depart_t"], sec["arrive_t"], gaps)
        dist[k] = R.sample(dr.t.values, dr.frac.values, sig[k]["t"])
        baro_only[k] = "barometer only" in sec["evidence"]
    return sig, lm, lm_h, dist, meta, baro_only


def groups(meta, sig):
    g = {}
    for k, m in meta.items():
        if k in sig and (m["dir"], m["from"], m["to"]) in TRACKS and m["clean"]:
            g.setdefault(f"{m['dir']} {m['from']}->{m['to']}", []).append(k)
    return {n: ks for n, ks in g.items() if len(ks) >= 2}


def length_of(key, L):
    a, b = key.split(":")[1].split("-")
    return L["-".join(sorted([a, b], key=lambda s: list(STATIONS).index(s)))]


def arc_chord(sig, dist, keys):
    """Path length / chord from heading, with constant speed (L0) and dead-reckoned speed (L1)."""
    out = {}
    for k in keys:
        h = np.radians(sig[k]["raw"]["head"])
        ok = np.isfinite(h)
        res = {}
        for name, ds in (("L0", np.ones(ok.sum())), ("L1", np.diff(np.r_[0, np.nan_to_num(dist[k])])[ok])):
            xy = np.column_stack([np.cumsum(ds * np.cos(h[ok])), np.cumsum(ds * np.sin(h[ok]))])
            res[name] = round(float(ds.sum() / np.linalg.norm(xy[-1] - xy[0])), 4)
        out[k] = res
    return out


# --------------------------------------------------------------------------- 3. evaluation
def warp_for(name, spec, sA, sB, dA, dB, weights=None, mask=None, pen=0.0):
    if name == "L0 time fraction":
        return R.GRID.copy()
    if name == "L1 dead reckoning":
        return P.l1_warp(dA, dB)
    chans, slack, leaky = spec
    if mask is not None and "head" in chans and not leaky:
        sA = P.bridge_heading(sA, mask)
    w = None if weights is None else [weights.get(c, 1.0) for c in chans]
    A, WA = P.stack(sA, chans, w)
    B, MB = P.stack(sB, chans)
    if mask is not None:
        for i, c in enumerate(chans):
            if c in CURVE:
                WA[i, mask] = 0
    return P.dtw_warp(A, WA, B, MB, step_penalty=pen, slack=slack)


def fix_estimate(name, fx, fy, fa):
    """No-DTW baselines from the other matched curve edges (fx on A, fy on B, sorted)."""
    if name == "L0 + offset":
        return fa + (float(np.median(fy - fx)) if len(fx) else 0.0)
    if name == "interp curve fixes":  # pinned at depart / arrive like the DTW
        return float(np.interp(fa, np.r_[0.0, fx, 1.0], np.r_[0.0, fy, 1.0]))
    if not len(fx):
        return fa
    if fa <= fx[0]:
        return fa + fy[0] - fx[0]
    if fa >= fx[-1]:
        return fa + fy[-1] - fx[-1]
    return float(np.interp(fa, fx, fy))


def evaluate(sig, lm, dist, pairs, L, sets=SPEC, weights=None, masks=MASKS, diag=True, pen=0.0):
    """Landmark rows for every ordered pair: err[set][mask] in metres; plus whole-path diagnostics."""
    rows, paths = [], []
    for A, B in pairs:
        La = length_of(A, L)
        match = P.match_landmarks(lm[A], lm[B])
        fx = np.array([lm[A][i]["frac"] for i, _ in match])
        fy = np.array([lm[B][j]["frac"] for _, j in match])
        o = np.argsort(fx)
        full = {n: warp_for(n, s, sig[A], sig[B], dist[A], dist[B], weights, pen=pen)
                for n, s in sets.items() if n not in FIXES}
        if diag and "all" in full:
            paths.append({"A": A, "B": B, **{n: round(float(np.mean(np.abs(full[n] - full["all"])) * La), 1)
                                             for n in full}})
        dB = dist[B].copy()
        ok = np.isfinite(dB)
        dB = np.interp(R.GRID, R.GRID[ok], dB[ok])
        for k, (i, j) in enumerate(match):
            fa, fb = fx[k], fy[k]
            row = {"A": A, "B": B, "ia": i, "jb": j, "kind": lm[A][i]["kind"], "sign": lm[A][i]["sign"],
                   "fa": round(fa, 4), "fb": round(fb, 4), "err": {}, "err_dist": {}}
            for n, s in sets.items():
                e = {}
                for mk, m in (("unmasked", None), *[(f"m{x}", x) for x in masks]):
                    if n in FIXES:
                        keep = (np.arange(len(fx)) != k) if m is None else (np.abs(fx - fa) > m)
                        keep = keep[o]
                        est = fix_estimate(n, fx[o][keep], fy[o][keep], fa)
                    elif m is None or s[0] is None or not set(s[0]) & set(CURVE):
                        est = P.warp_at(full[n], fa)  # mask is a no-op for sets that never see yaw / head
                    else:
                        est = P.warp_at(warp_for(n, s, sig[A], sig[B], dist[A], dist[B], weights,
                                                 np.abs(R.GRID - fa) <= m, pen), fa)
                    e[mk] = abs(est - fb) * La
                    if mk == HEAD:
                        row["err_dist"][n] = round(float(abs(np.interp(est, R.GRID, dB) - np.interp(fb, R.GRID, dB)) * La), 1)
                row["err"][n] = {q: round(float(v), 1) for q, v in e.items()}
            rows.append(row)
    return rows, paths


def tag_rows(rows, lm, anchors, sec_of):
    """Section and physical curve-edge id per row (A's landmark mapped onto the section's anchor run)."""
    maps = {}
    for r in rows:
        g = r["section"] = sec_of[r["A"]]
        anc = anchors[g]
        for run, idx in ((r["A"], r["ia"]), (r["B"], r["jb"])):
            if run not in maps:
                maps[run] = dict(P.match_landmarks(lm[run], lm[anc]))
            if idx in maps[run]:
                r["phys"] = f"{g}#{maps[run][idx]}"
                break
        else:
            r["phys"] = f"{g}#{r['A']}:{r['ia']}"
    return rows


# --------------------------------------------------------------------------- 4. statistics
def boot_weights(rows, seed=0, n=N_BOOT):
    """(n, rows) bootstrap weights: physical curve edges resampled with replacement, all their rows together."""
    ids = sorted({r["phys"] for r in rows})
    pos = {c: i for i, c in enumerate(ids)}
    cl = np.array([pos[r["phys"]] for r in rows])
    cnt = np.random.default_rng(seed).multinomial(len(ids), np.full(len(ids), 1 / len(ids)), size=n)
    return cnt[:, cl], len(ids)


def wmedian(x, W):
    """Median of x under each row of integer weights W (= median of the replicated sample)."""
    o = np.argsort(x)
    xs, cw = x[o], np.cumsum(W[:, o], 1)
    T = cw[:, -1]
    lo = (cw > ((T - 1) // 2)[:, None]).argmax(1)
    hi = (cw > (T // 2)[:, None]).argmax(1)
    return 0.5 * (xs[lo] + xs[hi])


def _x(rows, name, mask, key="err"):
    return np.array([r[key][name][mask] if key == "err" else r[key][name] for r in rows])


def summary(rows, name, mask, Wn):
    W, ncl = Wn
    x = _x(rows, name, mask)
    b = wmedian(x, W)
    return {"median_m": round(float(np.median(x)), 1), "mean_m": round(float(x.mean()), 1),
            "p90_m": round(float(np.percentile(x, 90)), 1), "n": len(x), "n_curve_edges": ncl,
            "ci95_median_m": [round(float(np.percentile(b, 2.5)), 1), round(float(np.percentile(b, 97.5)), 1)]}


def paired_delta(rows, a, b, mask, Wn):
    """median(err_a) - median(err_b) over the same rows; CI from the curve-edge bootstrap."""
    W, _ = Wn
    xa, xb = _x(rows, a, mask), _x(rows, b, mask)
    d = wmedian(xa, W) - wmedian(xb, W)
    lo, hi = np.percentile(d, [2.5, 97.5])
    eff = "no detectable effect" if lo <= 0 <= hi else ("worse" if lo > 0 else "better")
    return {"delta_median_m": round(float(np.median(xa) - np.median(xb)), 1),
            "ci95": [round(float(lo), 1), round(float(hi), 1)], "effect": eff}


def _channel(d, removed):
    """Restate a paired delta as the channel's own effect (removing a channel that helps raises the error)."""
    lo, hi = d["ci95"]
    helps = lo > 0 if removed else hi < 0
    d["channel_effect"] = "no detectable effect" if lo <= 0 <= hi else "channel helps" if helps else "channel hurts"
    return d


def scopes_of(rows, sections):
    return {**{s: [r for r in rows if r["section"] == s] for s in sections}, "overall": rows}


def aggregate(rows, sections, names, masks=MASKS):
    out = {}
    for scope, rs in scopes_of(rows, sections).items():
        Wn = boot_weights(rs)
        out[scope] = {n: {m: summary(rs, n, m, Wn) for m in ("unmasked", *[f"m{x}" for x in masks])} for n in names}
    return out


def ablation_deltas(rows, sections, mask=HEAD):
    out = {}
    for scope, rs in scopes_of(rows, sections).items():
        Wn = boot_weights(rs)
        pd_ = lambda a, b: paired_delta(rs, a, b, mask, Wn)
        out[scope] = {
            "leave_one_out_vs_all": {f: _channel(pd_(f"all-{f}", "all"), removed=True) for f in ALL},
            "add_to_yaw": {f: _channel(pd_(f"yaw+{f}", "yaw"), removed=False) for f in ALL if f != "yaw"},
            "alone_vs_L0": {f: pd_(f, "L0 time fraction") for f in ALL},
            "alone_vs_interp": {f: pd_(f, "interp curve fixes") for f in ALL},
            "all_vs_yaw": pd_("all", "yaw"), "yaw_vs_L0": pd_("yaw", "L0 time fraction"),
            "yaw_vs_L0_offset": pd_("yaw", "L0 + offset"), "yaw_vs_interp": pd_("yaw", "interp curve fixes"),
            "L1_vs_L0": pd_("L1 dead reckoning", "L0 time fraction"),
            "yaw_open_vs_pinned": pd_("yaw, open ends", "yaw"),
            "leaky_vs_bridged_heading": pd_("yaw+head, leaky heading", "yaw+head")}
    return out


PAIR_SETS = ["L0 time fraction", "L0 + offset", "interp curve fixes", "yaw", "yaw, open ends", "all"]


def per_pair(rows, baro_only):
    out = {}
    for r in rows:
        out.setdefault((r["section"], tuple(sorted((r["A"], r["B"])))), []).append(r)
    return [{"section": s, "runs": list(p), "n_rows": len(rs), "baro_only_boundary": [k for k in p if baro_only[k]],
             **{n: round(float(np.median(_x(rs, n, HEAD))), 1) for n in PAIR_SETS}}
            for (s, p), rs in out.items()]


def ends_vs_mid(rows, names):
    end = [r for r in rows if r["fa"] < 0.1 or r["fa"] > 0.9]
    mid = [r for r in rows if 0.1 <= r["fa"] <= 0.9]
    return {"n_end": len(end), "n_mid": len(mid),
            **{n: {"end_m": round(float(np.median(_x(end, n, HEAD))), 1),
                   "mid_m": round(float(np.median(_x(mid, n, HEAD))), 1)} for n in names}}


def mag_by_mode(rows, meta):
    out = {}
    for r in rows:
        ma, mb = meta[r["A"]]["mag_mode"], meta[r["B"]]["mag_mode"]
        out.setdefault(f"both {ma}" if ma == mb else "raw+calibrated", []).append(r)
    return {k: {"n": len(rs), **{n: round(float(np.median(_x(rs, n, HEAD))), 1)
                                 for n in ("L0 time fraction", "interp curve fixes", "yaw", "mag", "yaw+mag")}}
            for k, rs in out.items()}


def weight_sweep(sig, lm, dist, pairs, L, combo, sections, anchors, sec_of):
    """EXPLORATORY: per-channel weights for one combo, tuned on the same data (yaw fixed at 1)."""
    others = [c for c in combo if c != "yaw"]
    levels = [0.25, 1.0, 4.0] if len(others) > 3 else [0.25, 0.5, 1.0, 2.0, 4.0]
    res = []
    for ws in itertools.product(levels, repeat=len(others)):
        w = {"yaw": 1.0, **dict(zip(others, ws))}
        rows, _ = evaluate(sig, lm, dist, pairs, L, {"tuned": (combo, 0, False)}, w, masks=(0.08,), diag=False)
        tag_rows(rows, lm, anchors, sec_of)
        per = {s: float(np.median([r["err"]["tuned"][HEAD] for r in rows if r["section"] == s])) for s in sections}
        res.append({"weights": w, "median_m": float(np.median(_x(rows, "tuned", HEAD))), "per_section": per, "rows": rows})
    best = min(res, key=lambda d: d["median_m"])
    loso = {}  # leave-one-section-out: choose weights on the other sections, score on the held-out one
    for s in sections:
        pick = min(res, key=lambda d: np.median([r["err"]["tuned"][HEAD] for r in d["rows"] if r["section"] != s]))
        loso[s] = {"weights": pick["weights"], "median_m_heldout_section": round(pick["per_section"][s], 1)}
    return {"combo": combo, "levels": levels, "n_weightings": len(res), "best_weights": best["weights"],
            "best": summary(best["rows"], "tuned", HEAD, boot_weights(best["rows"])),
            "best_per_section": {s: round(v, 1) for s, v in best["per_section"].items()},
            "leave_one_section_out": loso,
            "note": "Weights tuned on the same landmarks they are scored on: optimistic. The leave-one-section-"
                    "out numbers choose weights on two sections and score the third."}


def penalty_sweep(sig, lm, dist, pairs, L, names, sections, anchors, sec_of, pens=(0.02, 0.05, 0.1, 0.2, 0.5)):
    """EXPLORATORY: non-diagonal step penalty (smoother warp) for a few sets, tuned on the same data."""
    out = {}
    for pen in pens:
        rows, _ = evaluate(sig, lm, dist, pairs, L, {n: SPEC[n] for n in names}, masks=(0.08,), diag=False, pen=pen)
        tag_rows(rows, lm, anchors, sec_of)
        out[str(pen)] = {sc: {n: round(float(np.median(_x(rs, n, HEAD))), 1) for n in names}
                         for sc, rs in scopes_of(rows, sections).items()}
    return out


# --------------------------------------------------------------------------- plots
INK, INK2, GRID_C, BLUE, ORANGE, GREY = "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6", "#eb6834", "#8d8c87"


def style(ax):
    ax.set_facecolor("#fcfcfb")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GREY)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(color=GRID_C, lw=0.8)
    ax.set_axisbelow(True)


def plot_landmarks(sig, lm, lm_h, meta, G):
    fig, axes = plt.subplots(len(G), 1, figsize=(11, 3.0 * len(G)), squeeze=False)
    for ax, (g, keys) in zip(axes[:, 0], G.items()):
        style(ax)
        extra = [k for k in STRESS if k in sig and meta[k]["dir"] == g.split()[0]
                 and f"{meta[k]['from']}->{meta[k]['to']}" == g.split()[1]]
        for i, k in enumerate(keys + extra):
            c = f"C{i}"
            ax.plot(R.GRID, sig[k]["raw"]["yaw"], color=c, lw=1.2, ls="-" if k in keys else ":",
                    label=k + ("" if k in keys else " (excluded, stress test)"))
            for d in lm[k]:
                ax.axvline(d["frac"], color=c, lw=0.8, ls="--" if d["kind"] == "on" else ":", alpha=0.8)
            if k in keys:
                yl = ax.get_ylim()
                for d in lm_h[k]:
                    ax.plot(d["frac"], yl[0] + 0.04 * (yl[1] - yl[0]) * (i + 1), "^" if d["kind"] == "on" else "v",
                            color=c, ms=4)
        ax.axhline(0, color=GREY, lw=0.6)
        for y in (0.6, -0.6):
            ax.axhline(y, color=GREY, lw=0.6, ls=":")
        ax.set_title(f"{g}: yaw rate and curve edges (lines: half-level edges, dashed = onset, dotted = end; "
                     "triangles: heading 15 / 85 % edges, robustness check)", fontsize=9, color=INK)
        ax.set_ylabel("yaw (deg/s)", color=INK2, fontsize=8)
        ax.legend(fontsize=7, loc="upper right", frameon=False)
    axes[-1, 0].set_xlabel("section fraction (L0 wall-clock)", color=INK2, fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "landmarks.png", dpi=110)
    plt.close(fig)


ORDER = ["L0 time fraction", "L0 + offset", "L1 dead reckoning", "interp curve fixes", "interp curve fixes, open ends",
         "yaw", "yaw, open ends", "head", "vib", "drive", "baro", "mag",
         "yaw+head", "yaw+head, leaky heading", "yaw+vib", "yaw+drive", "yaw+baro", "yaw+mag", "yaw+vib+drive",
         "yaw+vib+drive+mag", "all", "all, open ends", "all-yaw", "all-head", "all-vib", "all-drive", "all-baro",
         "all-mag"]


def plot_ablation(agg, sections):
    scopes = list(sections) + ["overall"]
    fig, axes = plt.subplots(1, len(scopes), figsize=(4.2 * len(scopes), 8.5), sharey=True)
    y = np.arange(len(ORDER))[::-1]
    for ax, sc in zip(axes, scopes):
        style(ax)
        for yi, n in zip(y, ORDER):
            s, u = agg[sc][n][HEAD], agg[sc][n]["unmasked"]
            c = GREY if n in BASELINES else ORANGE if n == HEADLINE else INK2 if "leaky" in n else BLUE
            ax.plot(s["ci95_median_m"], [yi, yi], color=c, lw=2, solid_capstyle="round",
                    ls=":" if "leaky" in n else "-")
            ax.plot(s["median_m"], yi, "o", color=c, ms=7, mec="#fcfcfb", mew=1.5, zorder=3)
            if u["median_m"] != s["median_m"]:
                ax.plot(u["median_m"], yi, "o", mfc="none", mec=c, ms=6, mew=1.2, zorder=3)
        for n, ls in (("L0 time fraction", "--"), ("interp curve fixes", ":")):
            ax.axvline(agg[sc][n][HEAD]["median_m"], color=GREY, ls=ls, lw=1)
        a = agg[sc]["yaw"][HEAD]
        note = "  [one run pair]" if sc == "WB BTH->LST" else ""
        ax.set_title(f"{sc}  ({a['n']} rows, {a['n_curve_edges']} curve edges){note}", fontsize=9, color=INK)
        ax.set_xlabel("median landmark error (m)", color=INK2, fontsize=8)
        ax.set_xlim(left=0)
    axes[0].set_yticks(y, ORDER, fontsize=8, color=INK)
    fig.suptitle("Held-out landmark error, yaw (+ re-integrated heading) masked +-0.08 of the section around each "
                 "curve edge. Filled dot = median, bar = 95 % CI (bootstrap over curve edges), hollow = unmasked.\n"
                 "Grey = baselines (dashed line: L0; dotted line: interpolating between the other curve edges, no DTW); "
                 "orange = headline DTW set (yaw); dotted dark = leaky heading, for comparison only", fontsize=9, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "ablation.png", dpi=110)
    plt.close(fig)


GAP_SETS = ["L0 time fraction", "L0 + offset", "interp curve fixes", "yaw", "yaw, open ends", "drive", "all"]


def plot_gap(agg, L):
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    style(ax)
    xs = np.array(MASKS) * 2 * L["LST-BTH"]
    ends = []
    for i, n in enumerate(GAP_SETS):
        med = np.array([agg["overall"][n][f"m{m}"]["median_m"] for m in MASKS])
        ci = np.array([agg["overall"][n][f"m{m}"]["ci95_median_m"] for m in MASKS])
        c = GREY if n in BASELINES else ORANGE if n == HEADLINE else BLUE
        ls = ["--", "-.", ":"][BASELINES.index(n) % 3] if n in BASELINES else "-"
        off = (i - len(GAP_SETS) / 2) * 6
        ax.errorbar(xs + off, med, yerr=[med - ci[:, 0], ci[:, 1] - med], color=c, ls=ls, marker="o", ms=4, lw=1.4,
                    elinewidth=0.6, capsize=0, alpha=1.0 if n in (HEADLINE, "interp curve fixes") else 0.75)
        ends.append([med[-1], n, c])
    ends.sort()
    for k in range(1, len(ends)):  # keep end labels at least 1.3 m apart
        ends[k][0] = max(ends[k][0], ends[k - 1][0] + 1.3)
    for yv, n, c in ends:
        ax.annotate(n, (xs[-1] + 30, yv), fontsize=7, color=c, va="center")
    ax.set_xlim(0, xs[-1] + 260)
    ax.set_xlabel("hidden gap around the curve edge (m on LST-BTH; the same section fraction elsewhere)",
                  color=INK2, fontsize=8)
    ax.set_ylabel("median landmark error, all sections (m)", color=INK2, fontsize=8)
    ax.set_title("Error vs the length of the hidden gap (bars: 95 % CI over curve edges; drive never sees yaw, "
                 "so it is flat)", fontsize=9, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "gap_length.png", dpi=110)
    plt.close(fig)


def plot_example(sig, lm, dist, A, B, chans, L, k_frac):
    fa_all = [d["frac"] for d in lm[A]]
    i = int(np.argmin(np.abs(np.array(fa_all) - k_frac)))
    j = dict(P.match_landmarks(lm[A], lm[B]))[i]
    fa, fb = lm[A][i]["frac"], lm[B][j]["frac"]
    mask = np.abs(R.GRID - fa) <= 0.08
    spec = (chans, 0, False)
    w = warp_for("x", spec, sig[A], sig[B], dist[A], dist[B], mask=mask)
    show = ["yaw"] + [c for c in chans if c != "yaw"]
    fig, ax = plt.subplots(len(show) + 1, 2, figsize=(13, 2.2 * (len(show) + 1)), sharex=True, squeeze=False)
    for r, c in enumerate(show):
        for col, xa in ((0, R.GRID), (1, w)):
            a_ = ax[r, col]
            style(a_)
            a_.plot(R.GRID, sig[B]["z"][c], color=GREY, lw=1.3, label=f"{B} (reference)")
            ya = sig[A]["z"][c].copy()
            a_.plot(xa, ya, color=BLUE, lw=1.1, label=f"{A} (held out)")
            if c in CURVE:
                a_.plot(xa, np.where(mask, ya, np.nan), color=ORANGE, lw=1.4, label="masked (unseen by DTW)")
            a_.set_ylabel(f"{c} (z)", color=INK2, fontsize=8)
            for d in lm[B]:
                a_.axvline(d["frac"], color=GREY, lw=0.5, ls=":")
    ax[0, 0].set_title(f"Before: {A} on its own L0 time fraction", fontsize=9, color=INK)
    ax[0, 1].set_title(f"After: {A} warped by DTW on {'+'.join(chans)} ({'+'.join(c for c in chans if c in CURVE)} "
                       f"masked around the {lm[A][i]['kind']} landmark)", fontsize=9, color=INK)
    ax[0, 0].legend(fontsize=7, frameon=False, loc="upper left")
    for col, ww, lab in ((0, R.GRID, "L0"), (1, w, "DTW")):
        a_ = ax[-1, col]
        style(a_)
        a_.plot(R.GRID, (ww - R.GRID) * L, color=BLUE, lw=1.3, label=f"{lab} warp - identity")
        a_.plot(R.GRID, (P.l1_warp(dist[A], dist[B]) - R.GRID) * L, color=GREY, lw=1, ls="--", label="L1 warp - identity")
        a_.plot([fa], [(fb - fa) * L], "o", color=INK, ms=6, label="truth at landmark")
        a_.plot([fa], [(P.warp_at(ww, fa) - fa) * L], "x", color=ORANGE, ms=9, mew=2,
                label=f"estimate (error {abs(P.warp_at(ww, fa) - fb) * L:.0f} m)")
        a_.set_ylabel("position shift (m)", color=INK2, fontsize=8)
        a_.set_xlabel("section fraction", color=INK2, fontsize=8)
        a_.legend(fontsize=7, frameon=False)
    for r in range(len(show)):
        ax[r, 1].axvline(fb, color=INK, lw=1)
        ax[r, 1].axvline(P.warp_at(w, fa), color=ORANGE, lw=1, ls="--")
    fig.tight_layout()
    fig.savefig(OUT / "alignment_example.png", dpi=110)
    plt.close(fig)
    return {"A": A, "B": B, "landmark": lm[A][i], "fb": fb, "set": chans,
            "error_m": round(float(abs(P.warp_at(w, fa) - fb) * L), 1), "L0_error_m": round(abs(fa - fb) * L, 1)}


# --------------------------------------------------------------------------- main
def main():
    t_start = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    tl = track_lengths()
    L = tl["used_m"]
    if "warning" in tl:
        print("WARNING:", tl["warning"])
    sig, lm, lm_h, dist, meta, baro_only = build()
    G = groups(meta, sig)
    tl["yaw_arc_chord"] = arc_chord(sig, dist, [k for ks in G.values() for k in ks])
    json.dump(tl, open(OUT / "track_lengths.json", "w"), indent=1)
    plot_landmarks(sig, lm, lm_h, meta, G)
    synth = [P.synthetic_check(np.random.default_rng(s)) for s in range(10)]
    synth_sum = {k: round(float(np.median([s[k] for s in synth])), 2) for k in synth[0]}
    synth_sum["worst_max_err_bins"] = round(max(s["max_err_bins"] for s in synth), 2)

    sec_of = {k: g for g, ks in G.items() for k in ks}
    anchors = {g: ks[0] for g, ks in G.items()}
    sections = list(G)
    pairs = [(a, b) for ks in G.values() for a, b in itertools.permutations(ks, 2)]
    rows, paths = evaluate(sig, lm, dist, pairs, L)
    tag_rows(rows, lm, anchors, sec_of)
    agg = aggregate(rows, sections, list(SPEC))
    deltas = ablation_deltas(rows, sections)
    plot_ablation(agg, sections)
    plot_gap(agg, L)

    # robustness: speed-independent heading-based edges as the truth
    rob_names = ["L0 time fraction", "L0 + offset", "interp curve fixes", "yaw", "yaw+head", "yaw+drive", "all"]
    hrows, _ = evaluate(sig, lm_h, dist, pairs, L, {n: SPEC[n] for n in rob_names}, masks=(0.08,), diag=False)
    tag_rows(hrows, lm_h, anchors, sec_of)
    hagg = aggregate(hrows, sections, rob_names, masks=(0.08,))
    robust = {sc: {n: {k: v for k, v in hagg[sc][n][HEAD].items() if k in ("median_m", "ci95_median_m", "n",
                                                                          "n_curve_edges")} for n in rob_names}
              for sc in hagg}

    sweep_all = weight_sweep(sig, lm, dist, pairs, L, ALL, sections, anchors, sec_of)
    pen = penalty_sweep(sig, lm, dist, pairs, L, ["yaw", "drive", "yaw+drive", "all"], sections, anchors, sec_of)

    # stress test: excluded R4 sections against the clean runs of the same section, both ways
    stress_pairs = []
    for s in STRESS:
        g = f"{meta[s]['dir']} {meta[s]['from']}->{meta[s]['to']}"
        clean = [k for k, m in meta.items() if k in sig and m["clean"] and f"{m['dir']} {m['from']}->{m['to']}" == g]
        anchors.setdefault(g, clean[0])
        for c in clean:
            sec_of[s] = sec_of[c] = g
            stress_pairs += [(s, c), (c, s)]
    st_names = ["L0 time fraction", "L1 dead reckoning", "interp curve fixes", "yaw", "yaw, open ends", "all"]
    srows, _ = evaluate(sig, lm, dist, stress_pairs, L, {n: SPEC[n] for n in st_names}, masks=(0.08,), diag=False)
    tag_rows(srows, lm, anchors, sec_of)
    stress = {}
    for role in ("held_out", "reference"):
        rs = [r for r in srows if r["A"].startswith("R4") == (role == "held_out")]
        for r in rs:
            r["R4_role"] = role
        if rs:
            Wn = boot_weights(rs)
            stress[f"R4_{role}"] = {n: {m: summary(rs, n, m, Wn) for m in ("unmasked", HEAD)} for n in st_names}

    example = plot_example(sig, lm, dist, "R3:LST-BTH", "R5:LST-BTH", ["yaw"], L["LST-BTH"], 0.5)
    pp = per_pair(rows, baro_only)
    ev = ends_vs_mid(rows, ["L0 time fraction", "interp curve fixes", "interp curve fixes, open ends", "yaw",
                            "yaw, open ends", "all"])
    mm = mag_by_mode(rows, meta)
    dist_check = {n: {"time_fraction_m": agg["overall"][n][HEAD]["median_m"],
                      "distance_fraction_m": round(float(np.median(_x(rows, n, None, "err_dist"))), 1)}
                  for n in ("L0 time fraction", "interp curve fixes", "yaw", "yaw+head", "drive", "all")}
    diag = {n: round(float(np.mean([p[n] for p in paths])), 1) for n in paths[0] if n not in ("A", "B")}
    out = {
        "settings": {"grid_N": R.N, "band_frac": P.BAND, "masks": MASKS, "headline_mask": HEAD,
                     "headline_set": HEADLINE, "masked_channels": CURVE, "open_end_slack_frac": SLACK,
                     "heading_under_mask": "re-integrated with yaw bridged linearly across the mask "
                                           "(position.bridge_heading); 'yaw+head, leaky heading' keeps the raw heading "
                                           "for comparison only",
                     "landmarks": "|yaw| > 0.6 deg/s for >= 4 s (1 s smoothing), edges at half the plateau level; "
                                  "censored edges dropped. Robustness truth: heading 15 / 85 % of each curve's turn",
                     "match": "by order if kinds/signs line up, else nearest same kind/sign within 0.1",
                     "weights": "equal (z-normalised channels)",
                     "error_metric": "|warp(fa) - fb| x straight-line section length, where fa, fb are L0 wall-clock "
                                     "fractions; 'distance_fraction_check' converts through B's dead-reckoned distance "
                                     "fraction instead",
                     "bootstrap": f"{N_BOOT} resamples of physical curve edges (all rows of an edge together: mirrored "
                                  "pairs and shared runs stay correlated). WB BTH->LST rests on one run pair.",
                     "fix_baselines": "use the other matched curve edges of the same pair outside the mask: 'L0 + "
                                      "offset' adds their median offset; 'interp curve fixes' interpolates linearly "
                                      "between them, pinned at depart/arrive ('open ends': constant offset past the "
                                      "outermost edge). No DTW.",
                     "track_lengths_m": L, "track_length_source": tl["source"], "features": P.__doc__},
        "synthetic_dtw_check": {"runs": 10, "median_of_runs": synth_sum,
                                "note": "3 smoothed-noise channels, known warp up to ~31 bins, noise sd 0.15; error in "
                                        "grid bins (1 bin = 1/399 of the section, ~5.5 m on LST-BTH). 'one_masked_plus_"
                                        "noise': a clean channel masked over 10 % with an unrelated random-walk channel "
                                        "left in; error inside the window."},
        "sections": {g: {"runs": ks, "pairs": [list(p) for p in pairs if p[0] in ks],
                         "baro_only_boundary": [k for k in ks if baro_only[k]],
                         "landmarks": {k: lm[k] for k in ks}, "heading_landmarks": {k: lm_h[k] for k in ks}}
                     for g, ks in G.items()},
        "summary": agg, "deltas": deltas, "per_pair": pp, "ends_vs_mid": ev, "mag_by_mode": mm,
        "robustness_heading_landmarks": robust, "distance_fraction_check": dist_check,
        "weight_sweep_EXPLORATORY": {"all": sweep_all}, "step_penalty_sweep_EXPLORATORY": pen,
        "stress_test_R4": stress, "whole_path_disagreement_vs_all_m_DIAGNOSTIC": diag, "alignment_example": example,
        "rows": rows, "stress_rows": srows, "path_rows": paths, "runtime_s": None}
    out["runtime_s"] = round(time.time() - t_start, 1)
    json.dump(out, open(OUT / "ablation.json", "w"), indent=1, default=float)
    write_log(agg, sections, deltas, pp, ev, sweep_all, pen, robust, tl)
    print_table(agg, sections, deltas, pp, ev, mm, robust, sweep_all, stress, synth_sum, dist_check, out["runtime_s"])
    print("step penalty (exploratory, masked median m):", json.dumps(pen))


LOG_STEPS = [("L0 time fraction", "L0 time fraction"),
             ("L0 + one offset fitted on the other curve edges (no DTW)", "L0 + offset"),
             ("L1 dead reckoning", "L1 dead reckoning"),
             ("Interpolate between the other curve edges (no DTW)", "interp curve fixes"),
             ("**L2 DTW yaw (headline)**", "yaw"),
             ("L2 DTW yaw, open ends (+-5 s)", "yaw, open ends"),
             ("L2 DTW yaw + heading (heading re-integrated, no leak)", "yaw+head"),
             ("L2 DTW yaw + drive", "yaw+drive"),
             ("L2 DTW all six, equal weights", "all")]


def write_log(agg, sections, deltas, pp, ev, sweep_all, pen, robust, tl):
    cols = ["EB LST->BTH"] + [s for s in sections if s != "EB LST->BTH"] + ["overall"]
    cell = lambda c, n, m=HEAD: (f"{agg[c][n][m]['median_m']:.0f} ({agg[c][n][m]['ci95_median_m'][0]:.0f}-"
                                 f"{agg[c][n][m]['ci95_median_m'][1]:.0f})")
    lines = ["# Positioning improvement log", "",
             "Held-out landmark error, median (95 % CI over curve edges) in metres. Truth = curve edges found from "
             "yaw. For each edge the held-out run's yaw is hidden +-0.08 of the section around it (~350 m gap on "
             "LST-BTH); heading is re-integrated so it cannot carry the hidden turn. All ordered pairs of clean "
             "runs; WB BTH->LST is a single run pair.", "",
             "| step | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for lab, n in LOG_STEPS:
        lines.append(f"| {lab} | " + " | ".join(cell(c, n) for c in cols) + " |")
    lines.append("| EXPLORATORY: all six, weights tuned on this data | " + " | ".join(
        f"{sweep_all['best_per_section'][c]:.0f}" if c != "overall" else f"{sweep_all['best']['median_m']:.0f}"
        for c in cols) + " |")
    lines.append("| EXPLORATORY: all six, step penalty 0.5 | " + " | ".join(f"{pen['0.5'][c]['all']:.0f}" for c in cols) + " |")
    lst = [p for p in pp if p["section"] == "EB LST->BTH"]
    d = deltas["overall"]
    lines += ["", "**Per run pair, EB LST->BTH** (median m: L0 / L0+offset / interp / DTW yaw; * = a boundary timed "
              "from the barometer only):",
              *[f"- {' vs '.join(p['runs'])}{' *' if p['baro_only_boundary'] else ''}: {p['L0 time fraction']:.0f} / "
                f"{p['L0 + offset']:.0f} / {p['interp curve fixes']:.0f} / {p['yaw']:.0f}" for p in lst], "",
              f"**Gap length** (overall median, mask half-width 0.04 / 0.08 / 0.12 / 0.16 / 0.24): DTW yaw " +
              " / ".join(f"{agg['overall']['yaw'][f'm{m}']['median_m']:.0f}" for m in MASKS) + " m; interpolation " +
              " / ".join(f"{agg['overall']['interp curve fixes'][f'm{m}']['median_m']:.0f}" for m in MASKS) + " m; L0 " +
              " / ".join(f"{agg['overall']['L0 time fraction'][f'm{m}']['median_m']:.0f}" for m in MASKS) + " m.",
              f"**Near a station** (fa < 0.1 or > 0.9, n = {ev['n_end']}) vs mid-section (n = {ev['n_mid']}): DTW yaw "
              f"{ev['yaw']['end_m']:.0f} vs {ev['yaw']['mid_m']:.0f} m; open ends {ev['yaw, open ends']['end_m']:.0f} vs "
              f"{ev['yaw, open ends']['mid_m']:.0f} m; L0 {ev['L0 time fraction']['end_m']:.0f} vs "
              f"{ev['L0 time fraction']['mid_m']:.0f} m.",
              f"**DTW yaw vs interpolation (no DTW)**: {d['yaw_vs_interp']['delta_median_m']:+.1f} m "
              f"[{d['yaw_vs_interp']['ci95'][0]:.1f}, {d['yaw_vs_interp']['ci95'][1]:.1f}], "
              f"{d['yaw_vs_interp']['effect']}. **DTW yaw vs L0**: {d['yaw_vs_L0']['delta_median_m']:+.1f} m "
              f"[{d['yaw_vs_L0']['ci95'][0]:.1f}, {d['yaw_vs_L0']['ci95'][1]:.1f}].",
              f"**Robustness, speed-independent heading edges as truth** (overall): L0 "
              f"{robust['overall']['L0 time fraction']['median_m']:.0f}, interp "
              f"{robust['overall']['interp curve fixes']['median_m']:.0f}, DTW yaw {robust['overall']['yaw']['median_m']:.0f}, "
              f"all six {robust['overall']['all']['median_m']:.0f} m.", "",
              "## Read this before quoting a number", "",
              "- With yaw hidden, DTW yaw is bridging the gap between the neighbouring curve edges, so it does about as "
              "well as interpolating between them with no DTW. These numbers measure curve fixes every ~150-350 m plus "
              "bridging, not positioning on long straight track (unmeasured; see the gap-length line).",
              "- Most of L0's error on LST->BTH is boundary timing (R3's departure is timed from the barometer only), "
              "not driving differences: compare the R1 vs R5 pair, whose boundaries are both observed.",
              "- The truth is derived from the same yaw signal; unmasked yaw numbers are close to circular.",
              "- 2-3 runs per section, one phone, one morning; the alignment is offline with both station ends known "
              "(the live demo needs an online, open-end variant). Exploratory rows are tuned on these same landmarks.",
              "- Metres = L0 fraction differences x straight-line length "
              f"({', '.join(f'{k} {v:.0f} m' for k, v in tl['used_m'].items())}; TfL lineStrings are too coarse). "
              "The real track is ~5 % (LST-BTH) to ~13 % (BTH-MLE) longer.", ""]
    (OUT / "improvement_log.md").write_text("\n".join(lines), encoding="utf-8")


def print_table(agg, sections, deltas, pp, ev, mm, robust, sweep_all, stress, synth, dist_check, rt):
    scopes = sections + ["overall"]
    print(f"synthetic DTW check: {synth}")
    print(f"{'set':30s}" + "".join(f"{s:>26s}" for s in scopes))
    for n in ORDER:
        cells = []
        for s in scopes:
            a, u = agg[s][n][HEAD], agg[s][n]["unmasked"]
            cells.append(f"{a['median_m']:6.1f} [{a['ci95_median_m'][0]:4.0f},{a['ci95_median_m'][1]:4.0f}] u{u['median_m']:4.1f}")
        print(f"{n:30s}" + "".join(f"{c:>26s}" for c in cells))
    print("n rows / curve edges:", {s: (agg[s]["yaw"][HEAD]["n"], agg[s]["yaw"][HEAD]["n_curve_edges"]) for s in scopes})
    print("gap sweep overall:", {n: [agg["overall"][n][f"m{m}"]["median_m"] for m in MASKS] for n in GAP_SETS})
    for s in scopes:
        d = deltas[s]
        print(s, "LOO:", {f: (v["delta_median_m"], v["ci95"], v["channel_effect"]) for f, v in d["leave_one_out_vs_all"].items()})
        print(s, "add to yaw:", {f: (v["delta_median_m"], v["ci95"], v["channel_effect"]) for f, v in d["add_to_yaw"].items()})
        print(s, "alone vs L0:", {f: (v["delta_median_m"], v["ci95"], v["effect"]) for f, v in d["alone_vs_L0"].items()})
        print(s, "alone vs interp:", {f: (v["delta_median_m"], v["ci95"], v["effect"]) for f, v in d["alone_vs_interp"].items()})
        print(s, "misc:", {k: (d[k]["delta_median_m"], d[k]["ci95"], d[k]["effect"]) for k in
                           ("all_vs_yaw", "yaw_vs_L0", "yaw_vs_L0_offset", "yaw_vs_interp", "L1_vs_L0",
                            "yaw_open_vs_pinned", "leaky_vs_bridged_heading")})
    print("per pair:", json.dumps(pp))
    print("ends vs mid:", json.dumps(ev))
    print("mag by mode:", json.dumps(mm))
    print("robustness (heading edges):", json.dumps({s: {n: v["median_m"] for n, v in r.items()} for s, r in robust.items()}))
    print("distance-fraction check:", json.dumps(dist_check))
    print("sweep all:", sweep_all["best_weights"], sweep_all["best"]["median_m"], sweep_all["best_per_section"],
          sweep_all["leave_one_section_out"])
    print("stress:", json.dumps({k: {n: (v[n][HEAD]["median_m"], v[n]["unmasked"]["median_m"]) for n in v}
                                 for k, v in stress.items()}))
    print(f"runtime {rt} s")


if __name__ == "__main__":
    main()
