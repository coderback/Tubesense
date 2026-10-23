"""Live demo: replay a ride through the online tracker, with the dot moving along a noise-coloured strip map.

    .venv/Scripts/python scripts/demo.py R5 --track "EB LST->BTH"            # window, real time
    .venv/Scripts/python scripts/demo.py R5 --track "EB LST->BTH" --speed 4  # 4x
    .venv/Scripts/python scripts/demo.py R5 --track "EB LST->BTH" --save outputs/online/demo_R5.mp4
    .venv/Scripts/python scripts/demo.py R5 --track "EB LST->BTH" --clip 40  # start 40 s after departure, unknown position
    .venv/Scripts/python scripts/demo.py R5 --track "EB BTH->MLE" --after 160  # second leg: arm after the BTH arrival

Everything the tracker shows at a moment uses only data up to that moment (online.stream_features
is causal). The noise overlay's audio-sensor sync uses the whole recording (fixed offset; for a new
ride it is computed on the fly), so that overlay is not strictly causal.
The departure detector is armed from the recording start (made on the platform) or from --after
seconds into it: a jump in horizontal acceleration only means "departure" when the train is known
to be at a platform (braking mid-section jumps too).
The map comes from outputs/online/maps.json (scripts/online_eval.py). If the run is one of the
map's runs, the map is rebuilt without it (leave-one-out), so the demo never sees its own answer.
A new ride only needs its phyphox .xlsx and .m4a in data/raw/ (named R<id>_<DIR>_<FROM>-<TO>):
sync and 1-s noise levels are computed on the fly if missing (levels only; audio is never saved).
--truth overlays the offline alignment (hollow marker) when the run is in outputs/sections.json.
"""
from __future__ import annotations

import argparse
import json
import shutil

import matplotlib
import numpy as np

from tubesense import online as O
from tubesense import repeat as R
from tubesense.ingest import ROOT, load_run
from tubesense.stops import STATIONS

STATION_NAMES = {k: v[0] for k, v in STATIONS.items()}
PRE_S = 8.0       # platform time shown before the departure
FPS = 8.0         # target drawn frames per second
LOCKED = 0.5      # posterior mass within 50 m of the estimate that counts as locked
HOLD = 8          # steps (2 s) the confident rule must hold before noise is painted
PAINT_BAND_M = 200.0  # paint live noise onto the map only when the whole 90 % band is this narrow:
                      # mid-section, two similar curves can hold a wrong mode with > LOCKED mass


def load_map(track: str, run_id: str) -> O.Template:
    maps = json.load(open(ROOT / "outputs" / "online" / "maps.json"))
    tpl = O.Template.from_json(maps[track])
    if not any(k.split(":")[0] == run_id for k in tpl.runs):
        return tpl
    srs = [s for s in O.section_runs().get(track, []) if s.key.split(":")[0] != run_id]
    if not srs:
        raise SystemExit(f"no other clean runs to build a leave-one-out map for {track}")
    noise = {s.key: ns for s in srs if (ns := O.noise_series(s)) is not None}
    print(f"leave-one-out: map rebuilt from {[s.key for s in srs]} (without {run_id})")
    return O.build_template(track, srs, tpl.length_m, noise)


def live_noise(run) -> tuple[np.ndarray, np.ndarray] | None:
    """(wall time of each 1-s audio bin centre, LAeq_1s) for the ride; computes sync/levels if missing."""
    from tubesense import noise, sync

    try:
        params = sync.load_params()
    except FileNotFoundError:
        params = {}
    if run.stem not in params:
        from tubesense.ingest import load_audio

        y, sr = load_audio(run)
        p = sync.sync_run(run, y, sr)
        p["wall0"] = float(run.segments().wall0.iloc[0])
        del y
        params[run.stem] = p
        sync._PARAMS = params
        print(f"synced on the fly: a = {p['a']:.2f} s ({p.get('method', 'xcorr')})")
    lv = noise.levels(run)
    p = params[run.stem]
    wall = p["wall0"] + (lv.t_audio.values + 0.5 - p["a"]) / p["b"]
    return wall, lv.LAeq_1s.values


def offline_truth(run, track: str, tpl: O.Template):
    """Offline alignment of the ride onto the map's base run (verification overlay), or None."""
    srs = O.section_runs(clean_only=False).get(track, [])
    a = next((s for s in srs if s.key.split(":")[0] == run.run_id), None)
    base = next((s for s in srs if s.key == tpl.base), None)
    if a is None or base is None:
        return None
    w = O.warp_onto(a, base)
    dur = a.sec["arrive_wall"] - a.sec["depart_wall"]
    return lambda wall: np.interp(wall, a.sec["depart_wall"] + R.GRID * dur, w, left=np.nan, right=np.nan)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="run id or file stem, e.g. R5")
    ap.add_argument("--track", required=True, help='e.g. "EB LST->BTH"')
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed factor")
    ap.add_argument("--clip", type=float, default=None, help="start this many s after departure, position unknown")
    ap.add_argument("--save", default=None, help=".mp4 or .gif; renders instead of opening a window")
    ap.add_argument("--after", type=float, default=0.0, help="arm the departure detector this many s into the recording")
    ap.add_argument("--truth", action="store_true", help="overlay the offline alignment (verification)")
    ap.add_argument("--snapshots", default=None, help="comma-separated s since departure: save PNG frames")
    args = ap.parse_args()
    if args.save or args.snapshots:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    run = load_run(args.run)
    tpl = load_map(args.track, run.run_id)
    L, N = tpl.length_m, len(tpl.yaw_mu)
    a_st, b_st = args.track.split()[1].split("->")
    feat = O.stream_features(run)  # gyro bias ~1e-4 rad/s on this phone: negligible, so none
    dep = O.departure_after(feat, feat.wall.iloc[0] + args.after)
    if dep is None:
        raise SystemExit("no departure detected after the detector was armed")
    print(f"departure detected at +{dep['wall_detect'] - feat.wall.iloc[0]:.1f} s into the recording "
          f"(onset back-dated {dep['wall_detect'] - dep['wall_onset']:.2f} s)")
    noise = live_noise(run)
    truth = offline_truth(run, args.track, tpl) if args.truth else None

    clip = args.clip is not None
    w_start = dep["wall_onset"] + (args.clip if clip else -PRE_S)
    w_end = min(feat.wall.iloc[-1], dep["wall_onset"] + 1.3 * tpl.duration_s + 10)
    steps = feat[(feat.wall >= w_start) & (feat.wall <= w_end)].reset_index(drop=True)
    tracker = O.Tracker(tpl, start="unknown" if clip else "depart")
    state = {"started": clip, "arrived": False, "est": None, "trail": [], "yaw": [], "last_noise": None}

    # ---- figure
    fig = plt.figure(figsize=(13, 7.2), facecolor="#111")
    gs = fig.add_gridspec(3, 2, height_ratios=[1.25, 1, 0.9], width_ratios=[3, 1.15], hspace=0.45, wspace=0.08)
    ax_map, ax_yaw, ax_txt = fig.add_subplot(gs[0, :]), fig.add_subplot(gs[1:, 0]), fig.add_subplot(gs[1:, 1])
    for ax in (ax_map, ax_yaw, ax_txt):
        ax.set_facecolor("#111")
        ax.tick_params(colors="0.8")
        for s in ax.spines.values():
            s.set_color("0.4")
    finite = tpl.noise_db[np.isfinite(tpl.noise_db)]
    vmin, vmax = np.percentile(finite, 5), np.percentile(finite, 99)
    cmap = plt.get_cmap("inferno")
    nb = len(tpl.noise_db)  # noise bins are equal map fractions: unequal metres on the dead-reckoned axis
    im = ax_map.pcolormesh(tpl.metres(np.linspace(0, 1, nb + 1)), [-0.25, 0.25], tpl.noise_db[None, :],
                           cmap=cmap, vmin=vmin, vmax=vmax)
    curve = np.abs(tpl.yaw_mu) > 0.6
    for i0, i1 in zip(*[np.flatnonzero(np.diff(np.r_[0, curve.astype(int), 0]) == s) for s in (1, -1)]):
        x0, x1 = tpl.metres(R.GRID[i0]), tpl.metres(R.GRID[min(i1, N - 1)])
        ax_map.add_patch(plt.Rectangle((x0, -0.42), x1 - x0, 0.1,
                                       color="0.55", lw=0))
    ax_map.text(0, -0.62, "curves", color="0.6", fontsize=8, va="center")
    ax_map.set_xlim(-0.03 * L, 1.03 * L)
    ax_map.set_ylim(-0.75, 0.95)
    ax_map.set_yticks([])
    ax_map.set_xlabel("metres from " + STATION_NAMES.get(a_st, a_st) + " (dead-reckoned, scaled to the straight-line length)", color="0.8")
    ax_map.text(0, 0.78, STATION_NAMES.get(a_st, a_st), color="w", ha="left", fontsize=11, weight="bold")
    ax_map.text(L, 0.78, STATION_NAMES.get(b_st, b_st), color="w", ha="right", fontsize=11, weight="bold")
    cb = fig.colorbar(im, ax=ax_map, orientation="vertical", fraction=0.02, pad=0.01)
    cb.set_label("map LAeq\ndBFS(A), relative", color="0.8", fontsize=8)
    cb.ax.tick_params(colors="0.8", labelsize=7)
    band, = ax_map.plot([], [], color="#4fc3f7", lw=6, alpha=0.45, solid_capstyle="butt")
    trail = ax_map.scatter([], [], c=[], cmap=cmap, vmin=vmin, vmax=vmax, s=36, marker="s", edgecolors="none")
    dot, = ax_map.plot([], [], "o", ms=16, mfc="#4fc3f7", mec="w", mew=2)
    tdot, = ax_map.plot([], [], "o", ms=20, mfc="none", mec="#66bb6a", mew=2)
    ax_map.text(L * 0.5, 0.52, "squares: noise this ride is measuring now, painted where the tracker puts it",
                color="0.6", fontsize=8, ha="center")
    title = fig.suptitle("", color="w", fontsize=13, x=0.06, ha="left")
    y_obs, = ax_yaw.plot([], [], color="#ffb74d", lw=1.4, label="phone yaw rate (live)")
    y_map, = ax_yaw.plot([], [], color="w", lw=1.0, alpha=0.8, label="map yaw at the estimated position")
    ax_yaw.set_ylim(-4, 4)
    ax_yaw.axhline(0, color="0.35", lw=0.8)
    ax_yaw.set_ylabel("deg/s  (+ = left)", color="0.8")
    ax_yaw.set_xlabel("seconds since departure", color="0.8")
    ax_yaw.legend(loc="upper left", fontsize=8, facecolor="#222", labelcolor="0.9", edgecolor="0.4")
    ax_txt.set_xticks([])
    ax_txt.set_yticks([])
    txt = ax_txt.text(0.05, 0.95, "", color="w", fontsize=10.5, va="top", family="monospace", transform=ax_txt.transAxes)

    title.set_text(f"Tubesense  |  {args.track.split()[0]}  {STATION_NAMES.get(a_st, a_st)} -> "
                   f"{STATION_NAMES.get(b_st, b_st)}  |  ride {run.run_id}" + ("  (clip)" if clip else ""))
    # fixed axes so live display can blit (only the moving artists are redrawn)
    ax_yaw.set_xlim(steps.wall.iloc[0] - dep["wall_onset"], steps.wall.iloc[-1] - dep["wall_onset"])

    def confident(e):  # also counts consecutive confident steps: painting needs HOLD of them
        ok = e["lock"] >= LOCKED and (tpl.metres(e["q95"]) - tpl.metres(e["q05"])) < PAINT_BAND_M
        state["held"] = state.get("held", 0) + 1 if ok else 0
        return state["held"] >= HOLD

    def update(i):
        r = steps.iloc[i]
        if not state["started"] and r.wall >= dep["wall_detect"]:
            # departure confirmed: start at the back-dated onset and catch up on the buffered steps (all past data)
            for b in steps[(steps.wall >= dep["wall_onset"]) & (steps.wall < r.wall)].itertuples():
                state["est"] = tracker.step(b.yaw, bool(b.valid), b.gyr > O.HANDLED)
            state["started"] = True
        if state["started"]:
            state["est"] = e = tracker.step(r.yaw, bool(r.valid), r.gyr > O.HANDLED)
            if e["map"] >= 0.995:
                state["arrived"] = True
        t = r.wall - dep["wall_onset"]
        state["yaw"].append((t, r.yaw if r.valid else np.nan))
        e = state["est"]
        pos = float(tpl.metres(e["map"])) if e else 0.0
        conf = confident(e) if e else False
        if noise is not None:
            k = np.searchsorted(noise[0], r.wall) - 1
            if k >= 0 and r.wall - noise[0][k] < 1.0:
                state["last_noise"] = noise[1][k]
                if conf and (not state["trail"] or state["trail"][-1][2] != k):
                    state["trail"].append((pos, noise[1][k], k))
        if state["trail"]:
            tr = np.array([(p, v) for p, v, _ in state["trail"]])
            trail.set_offsets(np.column_stack([tr[:, 0], np.full(len(tr), 0.38)]))
            trail.set_array(tr[:, 1])
        dot.set_data([pos], [0])
        if e:
            band.set_data(tpl.metres([e["q05"], e["q95"]]), [0, 0])
        if truth is not None:
            tv = truth(r.wall)
            tdot.set_data(([tpl.metres(tv)], [0]) if np.isfinite(tv) else ([], []))
        yy = np.array(state["yaw"])
        y_obs.set_data(yy[:, 0], yy[:, 1])
        if e:
            state.setdefault("mapyaw", []).append((t, float(np.interp(e["map"], R.GRID, tpl.yaw_mu))))
            my = np.array(state["mapyaw"])
            y_map.set_data(my[:, 0], my[:, 1])
        status = ("ARRIVED" if state["arrived"] else "TRACKING" if state["started"] else "AT PLATFORM: waiting for departure")
        if clip and e and e["lock"] < LOCKED and not state["arrived"]:
            status = "SEARCHING: matching curves"
        j = int(round(e["map"] * (N - 1))) if e else 0
        speed = tpl.speed(e["map"], e["speed_ratio"]) if e and state["started"] else 0.0
        mapdb = tpl.noise_db[min(int(e["map"] * len(tpl.noise_db)), len(tpl.noise_db) - 1)] if e else np.nan
        nz = state["last_noise"]
        lines = [status, "",
                 f"t {t:+7.1f} s since departure",
                 f"position {pos:7.0f} m" if e else "position       -",
                 f"  90%  {tpl.metres(e['q05']):5.0f}-{tpl.metres(e['q95']):5.0f} m" if e else "",
                 f"  {e['lock']:4.0%} within 50 m" if e else "",
                 f"speed  {speed * 3.6:6.0f} km/h" if e and state["started"] else "",
                 "",
                 f"noise now  {nz:6.1f} dBFS(A)" if nz is not None else "noise now      -",
                 f"map here   {mapdb:6.1f} dBFS(A)" if e else "",
                 "", "relative levels, uncalibrated", "no GPS: gyro curves + speed model"]
        if truth is not None and e:
            tv = truth(r.wall)
            if np.isfinite(tv):
                lines.append(f"vs offline alignment: {abs(tpl.metres(tv) - pos):3.0f} m")
        txt.set_text("\n".join(lines))
        return dot, band, trail, tdot, y_obs, y_map, txt

    if args.snapshots:
        out = ROOT / "outputs" / "online"
        want = sorted(float(s) for s in args.snapshots.split(","))
        for i in range(len(steps)):
            update(i)
            t = steps.wall.iloc[i] - dep["wall_onset"]
            while want and t >= want[0]:
                fig.savefig(out / f"demo_{run.run_id}_{want.pop(0):.0f}s.png", dpi=90, facecolor=fig.get_facecolor())
        return
    # the tracker sees every STEP; only every stride-th step is drawn (drawing is the slow part)
    stride = max(1, round(args.speed / (FPS * O.STEP)))
    shown = list(range(0, len(steps), stride))
    done = {"next": 0, "artists": ()}

    def frame(k):  # FuncAnimation may call a frame twice (init): only new steps reach the tracker
        for i in range(done["next"], shown[k] + 1):
            done["artists"] = update(i)
        done["next"] = max(done["next"], shown[k] + 1)
        return done["artists"]

    fps = args.speed / (stride * O.STEP)
    anim = FuncAnimation(fig, frame, frames=len(shown), interval=1000 / fps, blit=not args.save, repeat=False)
    if args.save:
        fps = max(1, round(fps))
        if args.save.endswith(".gif"):
            from matplotlib.animation import PillowWriter
            anim.save(args.save, writer=PillowWriter(fps=fps), dpi=80, savefig_kwargs={"facecolor": fig.get_facecolor()})
        else:
            if shutil.which("ffmpeg") is None:
                import imageio_ffmpeg
                matplotlib.rcParams["animation.ffmpeg_path"] = imageio_ffmpeg.get_ffmpeg_exe()
            anim.save(args.save, fps=fps, dpi=80, savefig_kwargs={"facecolor": fig.get_facecolor()})
        print("saved", args.save)
    else:
        plt.show()


if __name__ == "__main__":
    main()
