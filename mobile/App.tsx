// one import per weight, so only these five font files are bundled
import { PlusJakartaSans_400Regular } from '@expo-google-fonts/plus-jakarta-sans/400Regular';
import { PlusJakartaSans_500Medium } from '@expo-google-fonts/plus-jakarta-sans/500Medium';
import { PlusJakartaSans_600SemiBold } from '@expo-google-fonts/plus-jakarta-sans/600SemiBold';
import { PlusJakartaSans_700Bold } from '@expo-google-fonts/plus-jakarta-sans/700Bold';
import { PlusJakartaSans_800ExtraBold } from '@expo-google-fonts/plus-jakarta-sans/800ExtraBold';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { requestRecordingPermissionsAsync, setAudioModeAsync, useAudioStream } from 'expo-audio';
import { useFonts } from 'expo-font';
import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import { Barometer, DeviceMotion, Magnetometer, MagnetometerUncalibrated } from 'expo-sensors';
import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useWindowDimensions,
  type LayoutChangeEvent,
} from 'react-native';
import Svg, { Circle, Defs, Line, LinearGradient, Path, Polyline, Rect, Stop, Text as SvgText } from 'react-native-svg';

import { NOISE_STOPS, noiseColor, noiseT } from './src/iso';
import { LIGHT_MAP, RideMap, TUNNEL_MAP, type NoiseSample } from './src/RideMap';
import {
  SECTIONS,
  STATIONS,
  TimeGuess,
  fmtClock,
  fmtM,
  nextSection,
  parseServerPosition,
  reverseSection,
  type Position,
  type Section,
} from './src/route';

import {
  DEPART_N,
  DEPART_PUSH,
  DepartureGate,
  KnockDetector,
  PUSH_S,
  StopDetector,
  VibBand,
  angleDeg,
  enqueue,
  gravityDirection,
  ingestUrl,
  magnitudeMean,
  pressureChange,
  rotationVector,
  round,
  sessionId,
  summariseMotion,
  type MotionSample,
  type Vec3,
} from './src/metrics';
import { LevelMeter, OCT_LABELS, Spectrum, energyMeanDb } from './src/sound';

// Design v2 tokens, shared with webapp/static/css/app.css: a light "surface" look and a dark "tunnel" look.
type Palette = {
  bg: string; sheet: string; surface: string; surface2: string; line: string; ink: string; ink2: string; ink3: string;
  accent: string; onAccent: string; amber: string; onAmber: string; green: string; greenSoft: string; greenInk: string;
  red: string; redSoft: string; card: string; cardInk: string; cardSub: string; shadow: string;
};
const LIGHT: Palette = {
  bg: '#EEF1F8', sheet: '#FFFFFF', surface: '#FFFFFF', surface2: '#F2F4FA', line: '#E3E6F0', ink: '#121735', ink2: '#4B5275', ink3: '#646B8E',
  accent: '#3D5AFE', onAccent: '#FFFFFF', amber: '#FFC53D', onAmber: '#1F1203', green: '#12A27A', greenSoft: '#E3F6EF', greenInk: '#0E7A5C',
  red: '#C2333A', redSoft: '#FDECEC', card: '#121735', cardInk: '#FFFFFF', cardSub: '#C3CAE8', shadow: '#28326E',
};
const TUNNEL: Palette = {
  bg: '#03050C', sheet: '#0B1022', surface: '#0E1328', surface2: '#151B36', line: '#1E2649', ink: '#EEF1FB', ink2: '#A9B4DA', ink3: '#8A94BC',
  accent: '#5B7BFF', onAccent: '#FFFFFF', amber: '#FFC53D', onAmber: '#1F1203', green: '#34D399', greenSoft: '#0E2A24', greenInk: '#5EE0B0',
  red: '#FF9EA2', redSoft: '#2A1420', card: '#151B36', cardInk: '#FFFFFF', cardSub: '#A9B4DA', shadow: '#000000',
};
// Plus Jakarta Sans, one family per weight (React Native does not pick weights from one family)
const F = {
  r: 'PlusJakartaSans_400Regular',
  m: 'PlusJakartaSans_500Medium',
  sb: 'PlusJakartaSans_600SemiBold',
  b: 'PlusJakartaSans_700Bold',
  xb: 'PlusJakartaSans_800ExtraBold',
};

const HOST_KEY = 'tubesense.laptopHost';
const SECTION_KEY = 'tubesense.section';
const TUNNEL_KEY = 'tubesense.tunnel';
const CAL_KEY = 'tubesense.soundCalDb';
const DEFAULT_HOST = '172.20.10.3';
const AUDIO_HZ = 48000;
const CAL_S = 10; // calibrate against the energy mean of the last 10 s
const HSE_UPPER_DBA = 85; // HSE upper exposure action value, for colour only
const MOVED_DEG = 30; // gravity direction change over 10 s that counts as the phone being moved
const MOTION_MS = 10; // DeviceMotion: iOS caps at ~100 Hz
const MAG_MS = 50;
const BARO_MS = 200; // iOS delivers ~1 Hz regardless
const OUTBOX_MAX = 60;
const POST_TIMEOUT_MS = 2500;
const HEALTH_MS = 5000; // laptop check before a ride
const SPARK_S = 60;
const TOP = 54; // below the status bar

type Row = {
  source: 'tubesense-mobile';
  session: string;
  t: number;
  ts: string;
  yaw_rate_dps: number;
  acc_h_rms: number;
  vib_rms: number;
  b_abs_ut: number | null;
  b_raw_abs_ut: number | null;
  pressure_hpa: number | null;
  d_pressure_10s: number | null;
  la_dbfs: number | null;
  lafmax_dbfs: number | null;
  la_db: number | null;
  oct_dbfs: (number | null)[] | null; // octave bands 63 Hz-8 kHz, unweighted, dBFS
  peak_hz: number | null; // dominant frequency 100-2000 Hz
  peak_prom_db: number | null;
  vib_band_rms: number | null; // 3-40 Hz, as the pipeline's vib
  g_dir: [number, number, number] | null; // unit gravity (down) in the phone frame
  g_turn_10s_deg: number | null; // change of g_dir over 10 s
  push_5s: number | null; // departure gate: |5 s mean horizontal push|, m/s^2
  departed: boolean; // the train has pulled away (or the rider said so)
  state: 'moving' | 'stopped';
  knock: boolean;
  rates: { motion: number; mag: number; baro: number };
};

type Mic = 'off' | 'on' | 'denied' | 'error';

type Sub = { remove: () => void };

type Summary = {
  section: Section;
  coveredM: number;
  rideS: number;
  sent: number;
  queued: number;
  samples: NoiseSample[];
  calibrated: boolean;
  pos: Position | null;
};

export default function App() {
  const [fontsLoaded] = useFonts({
    PlusJakartaSans_400Regular,
    PlusJakartaSans_500Medium,
    PlusJakartaSans_600SemiBold,
    PlusJakartaSans_700Bold,
    PlusJakartaSans_800ExtraBold,
  });
  const [host, setHost] = useState(DEFAULT_HOST);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [row, setRow] = useState<Row | null>(null);
  const [yawHist, setYawHist] = useState<number[]>([]);
  const [knockAt, setKnockAt] = useState(0);
  const [net, setNet] = useState({ sent: 0, failed: 0, queued: 0, last: '' as string });
  const [sparkW, setSparkW] = useState(300);
  const [, setNow] = useState(0);
  const [section, setSection] = useState<Section>(SECTIONS[0]);
  const [tunnel, setTunnel] = useState(false);
  const [guessPos, setGuessPos] = useState<Position | null>(null);
  const [serverPos, setServerPos] = useState<Position | null>(null);
  const [details, setDetails] = useState(false);
  const [cal, setCal] = useState<number | null>(null);
  const [calRef, setCalRef] = useState('');
  const [mic, setMic] = useState<Mic>('off');
  const [departAt, setDepartAt] = useState<number | null>(null);
  const [paint, setPaint] = useState<NoiseSample[]>([]);
  const [stopS, setStopS] = useState(0);
  const [linked, setLinked] = useState<boolean | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [overview, setOverview] = useState(false);
  const [sheetH, setSheetH] = useState(330);
  const [barW, setBarW] = useState(320);

  const r = useRef({
    subs: [] as Sub[],
    timer: null as ReturnType<typeof setInterval> | null,
    session: '',
    t: 0,
    motion: [] as MotionSample[],
    mag: [] as Vec3[],
    magRaw: [] as Vec3[],
    meter: new LevelMeter(),
    spectrum: new Spectrum(),
    vibBand: new VibBand(),
    gHist: [] as Vec3[],
    gate: new DepartureGate(),
    departAt: null as number | null,
    laHist: [] as number[],
    cal: null as number | null,
    baroN: 0,
    baro: [] as { t: number; p: number }[],
    lastSampleT: 0,
    knocks: new KnockDetector(),
    knockFlag: false,
    stops: new StopDetector(),
    hState: { h: [0, 0, 0] as Vec3, t: null as number | null },
    outbox: [] as Row[],
    inFlight: false,
    host: DEFAULT_HOST,
    section: SECTIONS[0] as Section,
    guess: new TimeGuess(SECTIONS[0]),
    serverPos: null as Position | null,
    paint: [] as NoiseSample[],
    stopS: 0,
  });

  // Microphone: each PCM buffer goes through the A-weighting filter into a running sum and is then dropped. The stream
  // is never written to a file and never sent; only the per-second level (src/sound.ts) goes into the row.
  const { stream } = useAudioStream({
    sampleRate: AUDIO_HZ,
    channels: 1,
    encoding: 'float32',
    onBuffer: (b) => {
      if (!r.current.timer) return;
      const x = new Float32Array(b.data);
      r.current.meter.push(x, b.sampleRate, b.channels);
      r.current.spectrum.push(x, b.sampleRate, b.channels);
    },
  });

  useEffect(() => {
    AsyncStorage.getItem(HOST_KEY)
      .then((h) => {
        if (h) {
          setHost(h);
          r.current.host = h;
        }
      })
      .catch(() => {});
    AsyncStorage.getItem(SECTION_KEY)
      .then((k) => {
        const sec = SECTIONS.find((x) => x.key === k);
        if (sec) {
          setSection(sec);
          r.current.section = sec;
        }
      })
      .catch(() => {});
    AsyncStorage.getItem(TUNNEL_KEY)
      .then((v) => setTunnel(v === '1'))
      .catch(() => {});
    AsyncStorage.getItem(CAL_KEY)
      .then((v) => {
        const c = v == null ? NaN : Number(v);
        if (Number.isFinite(c)) {
          setCal(c);
          r.current.cal = c;
        }
      })
      .catch(() => {});
    return () => stopCapture();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // before a ride: is the laptop's web app reachable? (GET /api/health on the same port as the ingest)
  useEffect(() => {
    if (running || summary) return;
    let alive = true;
    const ping = async () => {
      const url = ingestUrl(host)?.replace('/api/live/ingest', '/api/health');
      if (!url) {
        if (alive) setLinked(false);
        return;
      }
      const ctl = new AbortController();
      const to = setTimeout(() => ctl.abort(), POST_TIMEOUT_MS);
      try {
        const res = await fetch(url, { signal: ctl.signal });
        if (alive) setLinked(res.ok);
      } catch {
        if (alive) setLinked(false);
      } finally {
        clearTimeout(to);
      }
    };
    ping();
    const id = setInterval(ping, HEALTH_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [host, running, summary]);

  // calibration: offset = reference meter dB(A) - our A-weighted dBFS, both over the same steady sound
  const calibrate = () => {
    const ref = Number(calRef.replace(',', '.'));
    const ours = energyMeanDb(r.current.laHist);
    if (!Number.isFinite(ref) || ref < 30 || ref > 130 || ours == null) return;
    const c = Math.round((ref - ours) * 10) / 10;
    setCal(c);
    r.current.cal = c;
    AsyncStorage.setItem(CAL_KEY, String(c)).catch(() => {});
  };
  const clearCal = () => {
    setCal(null);
    r.current.cal = null;
    AsyncStorage.removeItem(CAL_KEY).catch(() => {});
  };

  const onHost = (h: string) => {
    setHost(h);
    r.current.host = h;
    AsyncStorage.setItem(HOST_KEY, h).catch(() => {});
  };

  const flush = useCallback(async () => {
    const s = r.current;
    if (s.inFlight) return;
    const url = ingestUrl(s.host);
    if (!url) {
      setNet((n) => ({ ...n, queued: s.outbox.length, last: 'invalid laptop address' }));
      return;
    }
    s.inFlight = true;
    try {
      while (s.outbox.length) {
        const body = s.outbox[0];
        const ctl = new AbortController();
        const to = setTimeout(() => ctl.abort(), POST_TIMEOUT_MS);
        try {
          const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            signal: ctl.signal,
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          s.outbox.shift();
          // optional tracker estimate in the reply (CONTRACT.md, "position"); absent today unless the laptop adds it
          const reply = await res.json().catch(() => null);
          const est = parseServerPosition(reply, s.section);
          if (est) {
            s.serverPos = est;
            setServerPos(est);
          }
          setNet((n) => ({ ...n, sent: n.sent + 1, queued: s.outbox.length, last: 'ok' }));
        } catch (e) {
          const msg = e instanceof Error ? (e.name === 'AbortError' ? 'timeout' : e.message) : 'error';
          setNet((n) => ({ ...n, failed: n.failed + 1, queued: s.outbox.length, last: msg }));
          break; // retry on the next tick
        } finally {
          clearTimeout(to);
        }
      }
    } finally {
      s.inFlight = false;
    }
  }, []);

  const tick = useCallback(() => {
    const s = r.current;
    s.t += 1;
    const motion = s.motion;
    const mag = s.mag;
    const magRaw = s.magRaw;
    const baroN = s.baroN;
    s.motion = [];
    s.mag = [];
    s.magRaw = [];
    s.baroN = 0;
    const lv = s.meter.take();
    const sp = s.spectrum.take();
    s.vibBand.push(motion.map((x) => x.acc), 1000 / MOTION_MS);
    const vibBand = s.vibBand.take();
    const g = gravityDirection(motion);
    if (g) s.gHist = [...s.gHist, g].slice(-11);
    const gTurn = g && s.gHist.length === 11 ? angleDeg(g, s.gHist[0]) : null;
    const la = lv && Number.isFinite(lv.laeq) ? lv.laeq : null;
    if (la != null) s.laHist = [...s.laHist, la].slice(-CAL_S);

    for (const k of s.knocks.close(s.lastSampleT)) if (k.n === 2) s.knockFlag = true;
    const knock = s.knockFlag;
    s.knockFlag = false;

    const m = summariseMotion(motion, s.hState);
    const state = s.stops.step(m ? m.vib_rms : null, m ? m.yaw_rate_dps : null, m ? m.handled : false);
    // the ride starts when the phone feels the train pull away (src/metrics.ts DepartureGate), not on vibration
    const wasDeparted = s.gate.departed;
    if (s.gate.step(m ? m.h_mean : null, m ? m.handled : false) && !wasDeparted) {
      const credit = PUSH_S + DEPART_N - 1; // the push began this many seconds ago
      s.guess.depart(credit);
      s.departAt = Math.max(0, s.t - credit);
      setDepartAt(s.departAt);
    }
    const nowS = Date.now() / 1000;
    s.baro = s.baro.filter((b) => nowS - b.t <= 30);
    const lastBaro = s.baro.length ? s.baro[s.baro.length - 1] : null;

    const out: Row = {
      source: 'tubesense-mobile',
      session: s.session,
      t: s.t,
      ts: new Date().toISOString(),
      yaw_rate_dps: round(m ? m.yaw_rate_dps : 0, 3) ?? 0,
      acc_h_rms: round(m ? m.acc_h_rms : 0, 4) ?? 0,
      vib_rms: round(m ? m.vib_rms : 0, 4) ?? 0,
      b_abs_ut: round(magnitudeMean(mag), 2),
      b_raw_abs_ut: round(magnitudeMean(magRaw), 2),
      pressure_hpa: lastBaro && nowS - lastBaro.t <= 10 ? round(lastBaro.p, 3) : null,
      d_pressure_10s: round(pressureChange(s.baro, nowS, 10), 3),
      la_dbfs: round(la, 1),
      lafmax_dbfs: lv && Number.isFinite(lv.lafmax) ? round(lv.lafmax, 1) : null,
      la_db: la != null && s.cal != null ? round(la + s.cal, 1) : null,
      oct_dbfs: sp ? sp.oct : null,
      peak_hz: sp?.peakHz ?? null,
      peak_prom_db: sp?.peakPromDb ?? null,
      vib_band_rms: round(vibBand, 4),
      g_dir: g ? [round(g[0], 3) ?? 0, round(g[1], 3) ?? 0, round(g[2], 3) ?? 0] : null,
      g_turn_10s_deg: round(gTurn, 1),
      push_5s: round(s.gate.push, 3),
      departed: s.gate.departed,
      state,
      knock,
      rates: { motion: motion.length, mag: mag.length, baro: baroN },
    };
    s.outbox = enqueue(s.outbox, out, OUTBOX_MAX);
    if (s.gate.departed) s.guess.step(state);
    const gp = s.guess.position();
    setGuessPos(gp);
    // paint the line with this second's sound at the current position (phone level only, never audio)
    const here = s.serverPos ?? gp;
    const db = out.la_db ?? out.la_dbfs;
    if (s.gate.departed && here && db != null) {
      s.paint = [...s.paint, { sM: here.sM, db }];
      setPaint(s.paint);
    }
    s.stopS = s.gate.departed && state === 'stopped' ? s.stopS + 1 : 0;
    setStopS(s.stopS);
    setRow(out);
    setYawHist((h) => [...h, out.yaw_rate_dps].slice(-SPARK_S));
    if (knock) setKnockAt(Date.now());
    setNow(Date.now());
    flush();
  }, [flush]);

  const startCapture = async () => {
    setError(null);
    const s = r.current;
    try {
      const perm = await DeviceMotion.requestPermissionsAsync();
      if (!perm.granted) {
        setError('Motion & Fitness permission denied. Allow it for Expo Go in iOS Settings > Privacy & Security > Motion & Fitness.');
        return;
      }
      if (!(await DeviceMotion.isAvailableAsync())) {
        setError('DeviceMotion is not available on this device.');
        return;
      }
      const [magOk, magRawOk, baroOk] = await Promise.all([
        Magnetometer.isAvailableAsync().catch(() => false),
        MagnetometerUncalibrated.isAvailableAsync().catch(() => false),
        Barometer.isAvailableAsync().catch(() => false),
      ]);
      const micPerm = await requestRecordingPermissionsAsync().catch(() => null);

      s.session = sessionId();
      s.t = 0;
      s.motion = [];
      s.mag = [];
      s.magRaw = [];
      s.meter.reset();
      s.spectrum.reset();
      s.vibBand = new VibBand();
      s.gHist = [];
      s.laHist = [];
      s.baro = [];
      s.baroN = 0;
      s.knocks = new KnockDetector();
      s.knockFlag = false;
      s.stops = new StopDetector();
      s.hState = { h: [0, 0, 0], t: null };
      s.outbox = [];
      s.guess = new TimeGuess(s.section);
      s.gate = new DepartureGate();
      s.departAt = null;
      s.serverPos = null;
      s.paint = [];
      s.stopS = 0;
      setDepartAt(null);
      setGuessPos(null);
      setServerPos(null);
      setPaint([]);
      setStopS(0);
      setSummary(null);
      setOverview(false);
      setNet({ sent: 0, failed: 0, queued: 0, last: '' });
      setYawHist([]);
      setRow(null);

      DeviceMotion.setUpdateInterval(MOTION_MS);
      s.subs.push(
        DeviceMotion.addListener((d) => {
          const a = d.acceleration;
          const ag = d.accelerationIncludingGravity;
          const rr = d.rotationRate;
          if (!a || !ag || !rr) return;
          const t = typeof a.timestamp === 'number' && a.timestamp > 0 ? a.timestamp : Date.now() / 1000;
          const acc: Vec3 = [a.x, a.y, a.z];
          s.motion.push({
            t,
            acc,
            down: [ag.x - a.x, ag.y - a.y, ag.z - a.z],
            omega: rotationVector(rr, Platform.OS),
          });
          s.lastSampleT = t;
          for (const k of s.knocks.push(t, Math.hypot(a.x, a.y, a.z))) if (k.n === 2) s.knockFlag = true;
        }),
      );
      if (magOk) {
        Magnetometer.setUpdateInterval(MAG_MS);
        s.subs.push(Magnetometer.addListener((b) => s.mag.push([b.x, b.y, b.z])));
      }
      if (magRawOk) {
        // raw field (hard-iron bias left in), as in the phyphox Central runs
        MagnetometerUncalibrated.setUpdateInterval(MAG_MS);
        s.subs.push(MagnetometerUncalibrated.addListener((b) => s.magRaw.push([b.x, b.y, b.z])));
      }
      if (baroOk) {
        Barometer.setUpdateInterval(BARO_MS);
        s.subs.push(
          Barometer.addListener((b) => {
            s.baroN += 1;
            s.baro.push({ t: Date.now() / 1000, p: b.pressure });
          }),
        );
      }
      await activateKeepAwakeAsync('tubesense-capture').catch(() => {});
      s.timer = setInterval(tick, 1000);
      setRunning(true);
      if (micPerm?.granted) {
        try {
          await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
          await stream.start();
          setMic('on');
        } catch {
          setMic('error'); // motion capture carries on without sound
        }
      } else setMic('denied');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      stopCapture();
    }
  };

  function stopCapture() {
    const s = r.current;
    if (s.timer) clearInterval(s.timer);
    s.timer = null;
    s.subs.forEach((x) => x.remove());
    s.subs = [];
    s.motion = [];
    s.mag = [];
    s.magRaw = [];
    try {
      stream.stop();
    } catch {
      // not streaming
    }
    s.meter.reset();
    s.spectrum.reset();
    setMic('off');
    setAudioModeAsync({ allowsRecording: false }).catch(() => {});
    deactivateKeepAwake('tubesense-capture').catch(() => {});
    setRunning(false);
  }

  const knockOn = Date.now() - knockAt < 1500;
  const stopped = row?.state === 'stopped';
  const online = net.last === 'ok';
  const noSignal = !!net.last && !online;
  const P = tunnel ? TUNNEL : LIGHT;
  const st = useMemo(() => makeStyles(P), [P]);
  const { width, height } = useWindowDimensions();
  const pos = serverPos ?? (running ? guessPos : null);
  const remaining = pos ? Math.max(0, pos.lengthM - pos.sM) : section.lengthM;
  const from = STATIONS[section.from], to = STATIONS[section.to];
  const calibrated = row?.la_db != null;
  const soundDb = row?.la_db ?? row?.la_dbfs ?? null;
  const loud = row?.la_db != null && row.la_db >= HSE_UPPER_DBA;
  const calNow = energyMeanDb(r.current.laHist);

  const trainLeft = () => {
    const s = r.current;
    if (s.gate.departed) return;
    s.gate.force();
    s.guess.depart(0);
    s.departAt = s.t;
    setDepartAt(s.t);
    setGuessPos(s.guess.position());
  };
  const waiting = running && departAt == null;
  const arrived = !!pos && pos.lengthM - pos.sM < 1;
  const ridingS = departAt != null && row ? Math.max(0, row.t - departAt) : 0;
  // time left at the section's usual average speed (the phone does not measure speed)
  const toArriveS = pos && !arrived ? remaining / (section.lengthM / section.usualRunS) : null;

  const pickSection = (sec: Section) => {
    if (running) return;
    setSection(sec);
    r.current.section = sec;
    AsyncStorage.setItem(SECTION_KEY, sec.key).catch(() => {});
  };
  const toggleTunnel = () => {
    setTunnel((v) => {
      AsyncStorage.setItem(TUNNEL_KEY, v ? '0' : '1').catch(() => {});
      return !v;
    });
  };

  const endRide = () => {
    const s = r.current;
    const last = s.serverPos ?? s.guess.position();
    stopCapture();
    setSummary({
      section: s.section,
      coveredM: last ? last.sM : 0,
      rideS: s.departAt != null ? Math.max(0, s.t - s.departAt) : 0,
      sent: net.sent,
      queued: s.outbox.length,
      samples: s.paint,
      calibrated: s.cal != null,
      pos: last,
    });
  };
  const rideBack = () => {
    if (summary) pickSection(reverseSection(summary.section));
    setSummary(null);
  };

  if (!fontsLoaded) return <View style={{ flex: 1, backgroundColor: P.bg }} />;

  // ---------------------------------------------------------------- map framing per screen
  const sheetTop = height - sheetH;
  const phase: 'ready' | 'riding' | 'summary' = running ? 'riding' : summary ? 'summary' : 'ready';
  const mapSection = phase === 'summary' && summary ? summary.section : section;
  const mapPos = phase === 'summary' && summary ? summary.pos : pos;
  const frame =
    phase === 'ready'
      ? { left: 24, top: TOP + 150, right: width - 24, bottom: sheetTop - 24 }
      : phase === 'summary'
        ? { left: 24, top: TOP + 70, right: width - 24, bottom: sheetTop - 30 }
        : { left: 16, top: TOP + 150, right: width - 16, bottom: sheetTop - 30 };
  const anchor = { x: width * 0.5, y: frame.top + (frame.bottom - frame.top) * 0.58 };

  return (
    <View style={st.root}>
      <StatusBar style={tunnel ? 'light' : 'dark'} />
      <RideMap
        width={width}
        height={height}
        section={mapSection}
        pos={mapPos}
        theme={tunnel ? TUNNEL_MAP : LIGHT_MAP}
        mode={phase === 'riding' && !overview ? 'follow' : 'section'}
        frame={frame}
        anchor={anchor}
        samples={phase === 'summary' ? summary?.samples : paint}
        calibrated={phase === 'summary' ? summary?.calibrated : calibrated}
        tagSub={phase === 'summary' ? null : undefined}
      />

      {/* ---- top of the screen */}
      {phase === 'ready' && (
        <View style={[st.routeCard, { top: TOP }]}>
          <View style={st.routeRow}>
            <View style={[st.routeDot, { backgroundColor: P.accent }]} />
            <Text style={st.routeText} numberOfLines={1}>{from.name}</Text>
            <Roundel />
          </View>
          <View style={st.routeDivider} />
          <View style={st.routeRow}>
            <View style={[st.routeDot, st.routeDotHollow, { borderColor: P.ink }]} />
            <Pressable
              onPress={() => pickSection(nextSection(section) === section ? reverseSection(section) : nextSection(section))}
              accessibilityRole="button"
              accessibilityLabel={`Destination ${to.name}. Tap for the next station`}
              style={{ flex: 1 }}
            >
              <Text style={st.routeText} numberOfLines={1}>{to.name}</Text>
            </Pressable>
            <Pressable onPress={() => pickSection(reverseSection(section))} accessibilityRole="button" accessibilityLabel="Swap direction" style={st.swapBtn}>
              <Icon name="swap" color={P.ink} />
            </Pressable>
          </View>
        </View>
      )}

      {phase === 'riding' && (
        <View style={[st.topStack, { top: TOP }]} pointerEvents="box-none">
          {knockOn && (
            <View style={st.banner} accessibilityLiveRegion="polite">
              <View style={st.bannerIcon}><Icon name="hand" color={P.onAmber} /></View>
              <View style={{ flex: 1 }}>
                <Text style={st.bannerTitle}>Sync knock detected</Text>
                <Text style={st.bannerSub}>Ride start is lined up with the laptop</Text>
              </View>
            </View>
          )}
          {!knockOn && row?.g_turn_10s_deg != null && row.g_turn_10s_deg > MOVED_DEG && (
            <View style={st.banner} accessibilityLiveRegion="polite">
              <View style={st.bannerIcon}><Icon name="phone" color={P.onAmber} /></View>
              <View style={{ flex: 1 }}>
                <Text style={st.bannerTitle}>Phone moved · {Math.round(row.g_turn_10s_deg)}°</Text>
                <Text style={st.bannerSub}>Placement change flagged in the data</Text>
              </View>
            </View>
          )}
          <View style={st.topRow} pointerEvents="box-none">
            {waiting ? (
              <View style={st.navCard} accessibilityRole="summary">
                <View style={st.navIcon}><Icon name="clock" color="#FFFFFF" /></View>
                <Text style={st.navTitle}>Waiting to leave</Text>
                <Text style={st.navSub}>
                  {from.short} · push {row?.push_5s != null ? row.push_5s.toFixed(2) : '–'} of {DEPART_PUSH}
                </Text>
              </View>
            ) : stopped && !arrived && stopS > 0 ? (
              <View style={st.navCard} accessibilityRole="summary">
                <View style={[st.navIcon, { backgroundColor: P.amber }]}><Icon name="pause" color={P.onAmber} /></View>
                <Text style={st.navTitle}>Stopped in the tunnel</Text>
                <Text style={st.navSub}>{stopS} s · the dot holds still</Text>
              </View>
            ) : (
              <View style={st.navCard} accessibilityRole="summary">
                <View style={st.navIcon}><Icon name="up" color="#FFFFFF" /></View>
                <Text style={st.navBig}>{arrived ? 'Arrived' : fmtM(remaining)}</Text>
                <Text style={st.navTo} numberOfLines={1}>{to.name}</Text>
              </View>
            )}
            <SoundBadge P={P} st={st} db={soundDb} calibrated={calibrated} loud={loud} mic={mic} />
          </View>
        </View>
      )}

      {phase === 'summary' && (
        <Pressable onPress={() => setSummary(null)} style={[st.mapBtn, { position: 'absolute', top: TOP, left: 16 }]} accessibilityRole="button" accessibilityLabel="Close">
          <Icon name="close" color={P.ink} />
        </Pressable>
      )}

      {/* ---- round map buttons on the right: day/tunnel look, and follow/whole section while riding */}
      <View style={[st.mapBtns, { top: Math.max(TOP + 200, sheetTop - (phase === 'riding' ? 140 : 76)) }]} pointerEvents="box-none">
        <Pressable onPress={toggleTunnel} accessibilityRole="button" accessibilityLabel={tunnel ? 'Day map' : 'Tunnel map'} style={st.mapBtn}>
          <Icon name={tunnel ? 'sun' : 'moon'} color={P.ink} />
        </Pressable>
        {phase === 'riding' && (
          <Pressable
            onPress={() => setOverview((v) => !v)}
            accessibilityRole="button"
            accessibilityLabel={overview ? 'Follow the train' : 'Show the whole section'}
            style={st.mapBtn}
          >
            <Icon name={overview ? 'locate' : 'layers'} color={overview ? P.accent : P.ink} />
          </Pressable>
        )}
      </View>

      {/* ---- bottom sheet */}
      <View style={st.sheet} onLayout={(e: LayoutChangeEvent) => setSheetH(Math.round(e.nativeEvent.layout.height))}>
        <View style={st.grabber} />
        {phase === 'ready' && (
          <>
            <View style={st.sheetHead}>
              <View style={{ flex: 1 }}>
                <Text style={st.kicker}>Central line · {section.key.startsWith('EB') ? 'eastbound' : 'westbound'}</Text>
                <Text style={st.title}>{(section.lengthM / 1000).toFixed(1)} km · 1 stop</Text>
              </View>
              <Pressable
                onPress={() => setDetails(true)}
                accessibilityRole="button"
                accessibilityLabel="Laptop settings"
                style={[st.chip, { backgroundColor: linked ? P.greenSoft : P.surface2 }]}
              >
                <View style={[st.dot, { backgroundColor: linked ? P.green : linked === false ? P.red : P.ink3 }]} />
                <Text style={[st.chipText, { color: linked ? P.greenInk : P.ink2 }]}>
                  {linked ? 'Laptop linked' : linked === false ? 'No laptop' : 'Checking…'}
                </Text>
              </Pressable>
            </View>
            <View style={st.tips}>
              <View style={st.tip}>
                <View style={st.tipIcon}><Icon name="hand" color={P.accent} /></View>
                <Text style={st.tipText}>Knock the case twice after you start</Text>
              </View>
              <View style={st.tip}>
                <View style={st.tipIcon}><Icon name="mic" color={P.accent} /></View>
                <Text style={st.tipText}>Sound level only, never recorded or sent</Text>
              </View>
            </View>
            {error && <Text style={st.error}>{error}</Text>}
            <Pressable onPress={startCapture} style={({ pressed }) => [st.primary, pressed && { opacity: 0.85 }]} accessibilityRole="button">
              <Text style={st.primaryText}>Start ride</Text>
            </Pressable>
          </>
        )}

        {phase === 'riding' && (
          <>
            <View style={st.stats}>
              <Stat st={st} value={fmtClock(ridingS)} label={waiting ? 'not left yet' : 'riding'} />
              <Stat st={st} value={pos ? fmtM(pos.sM) : '–'} label={`from ${from.short}`} />
              <Stat st={st} value={toArriveS != null ? `~${fmtClock(toArriveS)}` : arrived ? '0:00' : '–'} label="to arrive" />
            </View>
            <View onLayout={(e: LayoutChangeEvent) => setBarW(e.nativeEvent.layout.width)}>
              <Progress width={barW} frac={pos ? pos.sM / pos.lengthM : 0} P={P} />
              <View style={st.ends}>
                <Text style={st.endText}>{from.short}</Text>
                <Text style={st.endText}>{to.short}</Text>
              </View>
            </View>
            {waiting && (
              <>
                <Text style={st.muted}>
                  The ride starts when the train pulls away from {from.short}. Shaking or carrying the phone doesn't count; a steady push does.
                </Text>
                <Pressable onPress={trainLeft} style={st.primarySm} accessibilityRole="button" accessibilityLabel="The train has left the station">
                  <Text style={st.primaryText}>Train left</Text>
                </Pressable>
              </>
            )}
            {noSignal ? (
              <>
                <Pressable onPress={() => setDetails(true)} style={st.linkCard} accessibilityRole="button">
                  <View style={st.linkIcon}><Icon name="wifiOff" color={P.ink2} /></View>
                  <View style={{ flex: 1 }}>
                    <Text style={st.linkTitle}>No signal to the laptop</Text>
                    <Text style={st.linkSub}>Normal underground. Sends when back.</Text>
                  </View>
                  <Text style={st.linkCount}>{net.queued}/{OUTBOX_MAX}</Text>
                </Pressable>
                <Pressable onPress={endRide} style={[st.stop, { alignSelf: 'stretch' }]} accessibilityRole="button">
                  <Text style={st.stopText}>End ride</Text>
                </Pressable>
              </>
            ) : (
              <View style={st.actions}>
                <Pressable onPress={() => setDetails(true)} style={st.pill} accessibilityRole="button">
                  <View style={[st.dot, { backgroundColor: online ? P.green : P.ink3 }]} />
                  <Text style={st.pillText} numberOfLines={1}>{online ? `Sending · ${net.sent} sent` : 'Connecting to the laptop…'}</Text>
                </Pressable>
                <Pressable onPress={endRide} style={st.stop} accessibilityRole="button">
                  <Text style={st.stopText}>End ride</Text>
                </Pressable>
              </View>
            )}
          </>
        )}

        {phase === 'summary' && summary && (
          <>
            <View style={st.sheetHead}>
              <View style={{ flex: 1 }}>
                <Text style={st.kicker}>
                  {STATIONS[summary.section.from].short} → {STATIONS[summary.section.to].short}
                </Text>
                <Text style={st.title}>Ride saved</Text>
              </View>
              <View style={[st.chip, { backgroundColor: summary.queued === 0 ? P.greenSoft : P.surface2 }]}>
                <View style={[st.dot, { backgroundColor: summary.queued === 0 ? P.green : P.amber }]} />
                <Text style={[st.chipText, { color: summary.queued === 0 ? P.greenInk : P.ink2 }]}>
                  {summary.queued === 0 ? 'All sent' : `${summary.queued} not sent`}
                </Text>
              </View>
            </View>
            <View style={st.boxes}>
              <Stat st={st} box value={`${(summary.coveredM / 1000).toFixed(2)} km`} label={summary.pos?.source === 'tracker' ? 'covered' : 'covered, est.'} />
              <Stat st={st} box value={fmtClock(summary.rideS)} label="ride time" />
              <Stat st={st} box value={`${summary.sent}`} label="seconds sent" />
            </View>
            <View onLayout={(e: LayoutChangeEvent) => setBarW(e.nativeEvent.layout.width)}>
              <Text style={st.label}>Noise along the way</Text>
              {summary.samples.length > 0 ? (
                <NoiseBars width={barW} samples={summary.samples} lengthM={summary.section.lengthM} calibrated={summary.calibrated} track={P.surface2} />
              ) : (
                <Text style={st.muted}>No sound measured on this ride (microphone off).</Text>
              )}
              <View style={st.ends}>
                <Text style={st.endText}>{STATIONS[summary.section.from].short}</Text>
                <Text style={st.endText}>{summary.calibrated ? 'dB(A)' : 'relative, not calibrated'}</Text>
                <Text style={st.endText}>{STATIONS[summary.section.to].short}</Text>
              </View>
            </View>
            <View style={st.actions}>
              <Pressable onPress={() => setSummary(null)} style={[st.secondary, { flex: 1 }]} accessibilityRole="button">
                <Text style={st.secondaryText}>Done</Text>
              </Pressable>
              <Pressable onPress={rideBack} style={[st.primarySm, { flex: 1 }]} accessibilityRole="button">
                <Text style={st.primaryText}>Ride back</Text>
              </Pressable>
            </View>
          </>
        )}
      </View>

      {/* ---- details: the sensor values and the laptop address (the old one-page screen, now on demand) */}
      {details && (
        <View style={st.overlay}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setDetails(false)} accessibilityLabel="Close details" />
          <View style={st.details}>
            <View style={st.grabber} />
            <View style={st.sheetHead}>
              <Text style={[st.title, { flex: 1 }]}>Sensor details</Text>
              <Pressable onPress={() => setDetails(false)} style={st.iconBtn} accessibilityRole="button" accessibilityLabel="Close">
                <Icon name="close" color={P.ink} />
              </Pressable>
            </View>
            <ScrollView contentContainerStyle={{ gap: 12, paddingBottom: 24 }} keyboardShouldPersistTaps="handled">
              <View style={st.grid}>
                <Stat st={st} box value={row?.pressure_hpa != null ? row.pressure_hpa.toFixed(2) : '–'} label="pressure, hPa" />
                <Stat st={st} box value={row?.d_pressure_10s != null ? (row.d_pressure_10s >= 0 ? '+' : '') + row.d_pressure_10s.toFixed(2) : '–'} label="change in 10 s, hPa" />
                <Stat st={st} box value={row?.b_abs_ut != null ? row.b_abs_ut.toFixed(1) : '–'} label="|B|, µT (calibrated)" />
                <Stat st={st} box value={row?.b_raw_abs_ut != null ? row.b_raw_abs_ut.toFixed(1) : '–'} label="|B|, µT (raw)" />
                <Stat st={st} box value={row ? row.vib_rms.toFixed(3) : '–'} label="vibration, m/s² rms" />
                <Stat st={st} box value={row?.vib_band_rms != null ? row.vib_band_rms.toFixed(3) : '–'} label="vibration 3–40 Hz, m/s²" />
                <Stat st={st} box value={row?.g_turn_10s_deg != null ? `${row.g_turn_10s_deg.toFixed(0)}°` : '–'} label="phone turned in 10 s" />
                <Stat st={st} box value={row ? row.acc_h_rms.toFixed(3) : '–'} label="horizontal accel., m/s² rms" />
                <Stat st={st} box value={row ? `${row.rates.motion}/${row.rates.mag}/${row.rates.baro}` : '–'} label="motion/mag/baro Hz" />
              </View>
              <View style={st.box}>
                <Text style={st.label}>Position</Text>
                <Text style={st.muted}>
                  {pos?.source === 'tracker'
                    ? 'From the laptop tracker (no GPS on the phone).'
                    : `Rough guess: moving time ÷ ${section.usualRunS} s (${section.usualNote}). Not GPS. "To arrive" uses the section's usual average speed; the phone does not measure speed.`}
                </Text>
              </View>
              <View style={st.box}>
                <Text style={st.label}>Sound · A-weighted, each second</Text>
                <Text style={st.value}>
                  {row?.la_dbfs != null
                    ? `${row.la_dbfs.toFixed(1)} dBFS · max ${row.lafmax_dbfs != null ? row.lafmax_dbfs.toFixed(1) : '–'}${row.la_db != null ? ` · ${row.la_db.toFixed(1)} dB(A)` : ''}`
                    : mic === 'denied'
                      ? 'Microphone not allowed (Settings > Expo Go > Microphone)'
                      : mic === 'error'
                        ? 'Microphone did not start'
                        : '–'}
                </Text>
                <Text style={st.muted}>
                  Loudest pitch {row?.peak_hz != null ? `${Math.round(row.peak_hz)} Hz (${row.peak_prom_db != null ? `+${row.peak_prom_db.toFixed(0)}` : '?'} dB over the rest)` : '–'}
                </Text>
                {row?.oct_dbfs && <Octaves values={row.oct_dbfs} width={sparkW} color={P.accent} ink={P.ink3} />}
                <Text style={st.muted}>
                  {cal != null ? `Calibrated: dB(A) = dBFS ${cal >= 0 ? '+' : ''}${cal.toFixed(1)}` : 'Not calibrated: dBFS is relative, not dB(A).'} Indicative, not a certified meter.
                </Text>
                <Text style={st.label}>Calibrate · hold the phone in a steady sound</Text>
                <View style={st.hostRow}>
                  <TextInput
                    style={st.input}
                    value={calRef}
                    onChangeText={setCalRef}
                    placeholder="reference meter, dB(A)"
                    placeholderTextColor={P.ink3}
                    keyboardType="decimal-pad"
                    accessibilityLabel="Reference meter reading in dB(A)"
                  />
                  <Pressable onPress={calibrate} disabled={calNow == null} style={[st.calBtn, calNow == null && { opacity: 0.4 }]} accessibilityRole="button">
                    <Text style={st.calBtnText}>Set</Text>
                  </Pressable>
                  {cal != null && (
                    <Pressable onPress={clearCal} style={st.calBtn} accessibilityRole="button">
                      <Text style={st.calBtnText}>Clear</Text>
                    </Pressable>
                  )}
                </View>
                <Text style={st.muted}>
                  Uses our mean over the last {CAL_S} s{calNow != null ? ` (${calNow.toFixed(1)} dBFS)` : ' (start a ride first)'}. Read the reference (e.g. NIOSH SLM) in the same sound.
                </Text>
              </View>
              <View style={st.box}>
                <Text style={st.label}>Turning · last 60 s</Text>
                <Text style={st.value}>{row ? `${row.yaw_rate_dps.toFixed(2)} °/s` : '–'}</Text>
                <View onLayout={(e: LayoutChangeEvent) => setSparkW(e.nativeEvent.layout.width)}>
                  <Spark data={yawHist} width={sparkW} height={80} line={P.line} stroke={P.accent} />
                </View>
                <Text style={st.muted}>+ = left turn (rotation about gravity)</Text>
              </View>
              <View style={st.box}>
                <Text style={st.label}>Laptop address</Text>
                <View style={st.hostRow}>
                  <TextInput
                    style={st.input}
                    value={host}
                    onChangeText={onHost}
                    placeholder={DEFAULT_HOST}
                    placeholderTextColor={P.ink3}
                    autoCapitalize="none"
                    autoCorrect={false}
                    keyboardType="numbers-and-punctuation"
                    accessibilityLabel="Laptop address"
                  />
                  <Text style={st.port}>:8770</Text>
                </View>
                <Text style={st.muted} numberOfLines={1}>{ingestUrl(host) ?? 'Enter an IP such as 172.20.10.3'}</Text>
                <Text style={st.muted}>
                  {!running ? (linked ? 'Laptop web app answers. ' : linked === false ? 'Laptop web app not reachable. ' : '') : ''}
                  sent {net.sent} · failed {net.failed} · queued {net.queued}/{OUTBOX_MAX}{net.last ? ` · last: ${net.last}` : ''}
                </Text>
                <Text style={st.muted}>Session {row?.session ?? 'starts at Start'} · random per Start, not tied to you or the phone</Text>
              </View>
              <Text style={st.foot}>Indicative values, not a certified instrument. The microphone gives a sound level only: audio is filtered in memory and dropped, never stored or sent. Raw samples never leave the phone; 1-second summaries only.</Text>
            </ScrollView>
          </View>
        </View>
      )}
    </View>
  );
}

function Stat({ st, value, label, box, color }: { st: Styles; value: string; label: string; box?: boolean; color?: string }) {
  return (
    <View style={box ? st.statBox : st.stat}>
      <Text style={[box ? st.statBoxValue : st.statValue, color ? { color } : null]} numberOfLines={1} adjustsFontSizeToFit>{value}</Text>
      <Text style={st.statLabel} numberOfLines={1}>{label}</Text>
    </View>
  );
}

function Roundel() {
  return (
    <View style={{ width: 26, height: 26, borderRadius: 13, backgroundColor: '#E1251B', alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ color: '#FFFFFF', fontSize: 12, fontFamily: F.xb }}>C</Text>
    </View>
  );
}

/** The sound now, as a round badge on the noise colour scale (design: orange circle, "REL. dB" when not calibrated). */
function SoundBadge({ P, st, db, calibrated, loud, mic }: { P: Palette; st: Styles; db: number | null; calibrated: boolean; loud: boolean; mic: Mic }) {
  const t = db == null ? 0 : noiseT(db, calibrated);
  const bg = db == null ? P.surface2 : noiseColor(t);
  const ink = db == null ? P.ink3 : t >= 0.35 ? '#FFFFFF' : '#121735';
  const value = db == null ? '–' : calibrated ? `${Math.round(db)}` : db.toFixed(1).replace('-', '−');
  const unit = db == null ? (mic === 'denied' ? 'MIC OFF' : mic === 'error' ? 'NO MIC' : 'SOUND') : calibrated ? (loud ? 'dB(A) LOUD' : 'dB(A)') : 'REL. dB';
  return (
    <View
      style={[st.soundBadge, { backgroundColor: bg }]}
      accessible
      accessibilityLabel={db == null ? 'Sound level not available' : `Sound now ${value} ${calibrated ? 'decibels A' : 'relative decibels, not calibrated'}`}
    >
      <Text style={[st.soundValue, { color: ink }]} numberOfLines={1} adjustsFontSizeToFit>{value}</Text>
      <Text style={[st.soundUnit, { color: ink }]}>{unit}</Text>
    </View>
  );
}

/** Progress along the section: the noise colour scale, the part still to go dimmed, and a knob at the position. */
function Progress({ width, frac, P }: { width: number; frac: number; P: Palette }) {
  const h = 26, bar = 10, y = (h - bar) / 2;
  const x = Math.min(width - 11, Math.max(11, Math.min(1, Math.max(0, frac)) * width));
  return (
    <Svg width={width} height={h}>
      <Defs>
        <LinearGradient id="noise" x1="0" y1="0" x2="1" y2="0">
          {NOISE_STOPS.map((c, i) => (
            <Stop key={c} offset={i / (NOISE_STOPS.length - 1)} stopColor={c} />
          ))}
        </LinearGradient>
      </Defs>
      <Rect x={0} y={y} width={width} height={bar} rx={bar / 2} fill="url(#noise)" />
      <Rect x={x} y={y} width={Math.max(0, width - x)} height={bar} rx={bar / 2} fill={P.surface2} opacity={0.85} />
      <Circle cx={x} cy={h / 2} r={10} fill="#FFFFFF" stroke={P.accent} strokeWidth={4} />
    </Svg>
  );
}

/** Sound along the way, binned by distance; bars on the noise colour scale. */
function NoiseBars({ width, samples, lengthM, calibrated, track }: { width: number; samples: NoiseSample[]; lengthM: number; calibrated: boolean; track: string }) {
  const n = 28, h = 72, gap = 4;
  const bins: number[][] = Array.from({ length: n }, () => []);
  for (const s of samples) bins[Math.min(n - 1, Math.max(0, Math.floor((s.sM / lengthM) * n)))].push(s.db);
  const bw = (width - gap * (n - 1)) / n;
  return (
    <Svg width={width} height={h} accessibilityLabel="Noise along the way">
      {bins.map((b, i) => {
        if (!b.length) return <Rect key={i} x={i * (bw + gap)} y={h - 4} width={bw} height={4} rx={2} fill={track} />;
        const db = energyMeanDb(b) ?? b[0];
        const t = Math.min(1, Math.max(0, noiseT(db, calibrated)));
        const bh = Math.max(6, 8 + t * (h - 8));
        return <Rect key={i} x={i * (bw + gap)} y={h - bh} width={bw} height={bh} rx={Math.min(4, bw / 2)} fill={noiseColor(t)} />;
      })}
    </Svg>
  );
}

type IconName = 'swap' | 'sun' | 'moon' | 'close' | 'hand' | 'mic' | 'up' | 'clock' | 'pause' | 'locate' | 'layers' | 'wifiOff' | 'phone';
function Icon({ name, color }: { name: IconName; color: string }) {
  const p = { stroke: color, strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, fill: 'none' };
  return (
    <Svg width={20} height={20} viewBox="0 0 24 24">
      {name === 'swap' && <Path d="M7 4v16M7 4L3 8M7 4l4 4M17 20V4M17 20l-4-4M17 20l4-4" {...p} />}
      {name === 'sun' && (
        <>
          <Circle cx={12} cy={12} r={4} {...p} />
          <Path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" {...p} />
        </>
      )}
      {name === 'moon' && <Path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" {...p} />}
      {name === 'close' && <Path d="M6 6l12 12M18 6L6 18" {...p} />}
      {name === 'hand' && <Path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V11m0-6.5V4a1.5 1.5 0 0 1 3 0v7m0-5.5a1.5 1.5 0 0 1 3 0V12m0-4.5a1.5 1.5 0 0 1 3 0V15a7 7 0 0 1-7 7h-1a7 7 0 0 1-5.6-2.8L4 16.5a1.6 1.6 0 0 1 2.4-2.1L8 16" {...p} />}
      {name === 'mic' && <Path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" {...p} />}
      {name === 'up' && <Path d="M12 20V5M5 11l7-7 7 7" {...p} strokeWidth={2.6} />}
      {name === 'clock' && (
        <>
          <Circle cx={12} cy={12} r={8.5} {...p} />
          <Path d="M12 7.5V12l3 2" {...p} />
        </>
      )}
      {name === 'pause' && <Path d="M9 6v12M15 6v12" {...p} strokeWidth={2.8} />}
      {name === 'locate' && (
        <>
          <Circle cx={12} cy={12} r={4} {...p} />
          <Path d="M12 2v3M12 19v3M2 12h3M19 12h3" {...p} />
        </>
      )}
      {name === 'layers' && <Path d="M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5" {...p} />}
      {name === 'wifiOff' && <Path d="M3 3l18 18M8.5 16.4a5 5 0 0 1 7 0M5 12.9a10 10 0 0 1 4.2-2.5M14.8 10.4A10 10 0 0 1 19 12.9M2 9.3a15 15 0 0 1 4.3-2.7M12 20h.01" {...p} />}
      {name === 'phone' && (
        <>
          <Rect x={7} y={2.5} width={10} height={19} rx={2.5} {...p} />
          <Path d="M11 18.5h2" {...p} />
        </>
      )}
    </Svg>
  );
}

function Octaves({ values, width, color, ink }: { values: (number | null)[]; width: number; color: string; ink: string }) {
  // octave bands as bars, 60 dB range under the loudest band
  const h = 70, lab = 14, n = values.length, gap = 6;
  const ok = values.filter((v): v is number => v != null);
  const top = ok.length ? Math.max(...ok) : 0;
  const bw = (width - gap * (n - 1)) / n;
  return (
    <Svg width={width} height={h + lab}>
      {values.map((v, i) => {
        const bh = v == null ? 0 : Math.max(2, (h * (60 + v - top)) / 60);
        return <Rect key={i} x={i * (bw + gap)} y={h - bh} width={bw} height={bh} rx={3} fill={color} opacity={0.85} />;
      })}
      {OCT_LABELS.map((l, i) => (
        <SvgText key={l} x={i * (bw + gap) + bw / 2} y={h + lab - 2} fontSize={10} fill={ink} textAnchor="middle">
          {l}
        </SvgText>
      ))}
    </Svg>
  );
}

function Spark({ data, width, height, line, stroke }: { data: number[]; width: number; height: number; line: string; stroke: string }) {
  const lim = Math.max(2, ...data.map((v) => Math.abs(v)));
  const x = (i: number) => (i / (SPARK_S - 1)) * width;
  const y = (v: number) => height / 2 - (v / lim) * (height / 2 - 4);
  const off = SPARK_S - data.length;
  const pts = data.map((v, i) => `${x(i + off).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  return (
    <Svg width={width} height={height}>
      <Line x1={0} y1={height / 2} x2={width} y2={height / 2} stroke={line} strokeWidth={1} />
      {data.length > 1 && <Polyline points={pts} fill="none" stroke={stroke} strokeWidth={2.5} />}
    </Svg>
  );
}

type Styles = ReturnType<typeof makeStyles>;
function makeStyles(P: Palette) {
  const float = { shadowColor: P.shadow, shadowOpacity: 0.22, shadowRadius: 24, shadowOffset: { width: 0, height: 12 }, elevation: 8 };
  return StyleSheet.create({
    root: { flex: 1, backgroundColor: P.bg },
    // AppStart: route card
    routeCard: { position: 'absolute', left: 16, right: 16, backgroundColor: P.sheet, borderRadius: 24, paddingLeft: 18, paddingRight: 12, paddingVertical: 8, ...float },
    routeRow: { flexDirection: 'row', alignItems: 'center', gap: 14, minHeight: 50 },
    routeDot: { width: 12, height: 12, borderRadius: 6 },
    routeDotHollow: { backgroundColor: 'transparent', borderWidth: 2.5 },
    routeText: { color: P.ink, fontSize: 17, fontFamily: F.b, flex: 1 },
    routeDivider: { height: 1, backgroundColor: P.line, marginLeft: 26, marginRight: 4 },
    swapBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: P.surface2, alignItems: 'center', justifyContent: 'center' },
    iconBtn: { width: 44, height: 44, borderRadius: 14, backgroundColor: P.surface2, alignItems: 'center', justifyContent: 'center' },
    // AppRiding / AppTunnel: overlays on the map
    topStack: { position: 'absolute', left: 16, right: 16, gap: 12 },
    topRow: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between' },
    banner: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: '#FFC53D', borderRadius: 20, paddingHorizontal: 14, paddingVertical: 12, ...float },
    bannerIcon: { width: 38, height: 38, borderRadius: 12, backgroundColor: 'rgba(31,18,3,0.10)', alignItems: 'center', justifyContent: 'center' },
    bannerTitle: { color: '#1F1203', fontSize: 15, fontFamily: F.xb },
    bannerSub: { color: '#1F1203', fontSize: 12, fontFamily: F.sb, opacity: 0.8 },
    navCard: { width: 222, backgroundColor: P.card, borderRadius: 24, paddingHorizontal: 18, paddingTop: 14, paddingBottom: 16, gap: 2, ...float },
    navIcon: { width: 36, height: 36, borderRadius: 12, backgroundColor: P.accent, alignItems: 'center', justifyContent: 'center', marginBottom: 8 },
    navBig: { color: P.cardInk, fontSize: 32, fontFamily: F.xb, letterSpacing: -1, fontVariant: ['tabular-nums'] },
    navTo: { color: P.cardSub, fontSize: 17, fontFamily: F.b },
    navTitle: { color: P.cardInk, fontSize: 17, fontFamily: F.xb, letterSpacing: -0.4 },
    navSub: { color: P.cardSub, fontSize: 14, fontFamily: F.sb, fontVariant: ['tabular-nums'] },
    soundBadge: { width: 96, height: 96, borderRadius: 48, borderWidth: 5, borderColor: P.sheet, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 8, ...float },
    soundValue: { fontSize: 24, fontFamily: F.xb, letterSpacing: -0.6, fontVariant: ['tabular-nums'] },
    soundUnit: { fontSize: 10, fontFamily: F.xb, letterSpacing: 0.6 },
    mapBtns: { position: 'absolute', right: 16, gap: 12 },
    mapBtn: { width: 52, height: 52, borderRadius: 26, backgroundColor: P.sheet, alignItems: 'center', justifyContent: 'center', ...float },
    // sheet
    sheet: { position: 'absolute', left: 0, right: 0, bottom: 0, backgroundColor: P.sheet, borderTopLeftRadius: 30, borderTopRightRadius: 30, paddingHorizontal: 20, paddingTop: 10, paddingBottom: 34, gap: 16, ...float },
    grabber: { alignSelf: 'center', width: 40, height: 5, borderRadius: 3, backgroundColor: P.line },
    sheetHead: { flexDirection: 'row', alignItems: 'center', gap: 12 },
    kicker: { color: P.ink3, fontSize: 13, fontFamily: F.sb },
    title: { color: P.ink, fontSize: 28, fontFamily: F.xb, letterSpacing: -0.8 },
    chip: { flexDirection: 'row', alignItems: 'center', gap: 6, height: 34, paddingHorizontal: 12, borderRadius: 17 },
    chipText: { fontSize: 13, fontFamily: F.b },
    tips: { flexDirection: 'row', gap: 10 },
    tip: { flex: 1, backgroundColor: P.surface2, borderRadius: 18, padding: 12, gap: 8 },
    tipIcon: { width: 34, height: 34, borderRadius: 11, backgroundColor: P.sheet, alignItems: 'center', justifyContent: 'center' },
    tipText: { color: P.ink2, fontSize: 13, lineHeight: 18, fontFamily: F.sb },
    primary: { height: 60, borderRadius: 20, backgroundColor: P.accent, alignItems: 'center', justifyContent: 'center',
      shadowColor: P.accent, shadowOpacity: 0.35, shadowRadius: 16, shadowOffset: { width: 0, height: 8 }, elevation: 6 },
    primarySm: { height: 52, borderRadius: 18, backgroundColor: P.accent, alignItems: 'center', justifyContent: 'center' },
    primaryText: { color: P.onAccent, fontSize: 17, fontFamily: F.xb },
    secondary: { height: 52, borderRadius: 18, backgroundColor: P.surface2, alignItems: 'center', justifyContent: 'center' },
    secondaryText: { color: P.ink, fontSize: 17, fontFamily: F.xb },
    error: { color: P.red, fontSize: 14, fontFamily: F.m },
    stats: { flexDirection: 'row' },
    stat: { flex: 1, alignItems: 'center' },
    statValue: { color: P.ink, fontSize: 24, fontFamily: F.xb, letterSpacing: -0.6, fontVariant: ['tabular-nums'] },
    statLabel: { color: P.ink3, fontSize: 12, fontFamily: F.sb },
    ends: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 4 },
    endText: { color: P.ink3, fontSize: 12, fontFamily: F.sb },
    muted: { color: P.ink3, fontSize: 12, lineHeight: 17, fontFamily: F.m },
    actions: { flexDirection: 'row', gap: 10, alignItems: 'center' },
    pill: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8, height: 52, paddingHorizontal: 16, borderRadius: 26, backgroundColor: P.surface2 },
    pillText: { color: P.ink2, fontSize: 14, fontFamily: F.b, flexShrink: 1 },
    stop: { height: 52, paddingHorizontal: 24, borderRadius: 18, backgroundColor: P.redSoft, alignItems: 'center', justifyContent: 'center' },
    stopText: { color: P.red, fontSize: 16, fontFamily: F.xb },
    linkCard: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: P.surface2, borderRadius: 20, padding: 14 },
    linkIcon: { width: 40, height: 40, borderRadius: 13, backgroundColor: P.sheet, alignItems: 'center', justifyContent: 'center' },
    linkTitle: { color: P.ink, fontSize: 15, fontFamily: F.xb },
    linkSub: { color: P.ink3, fontSize: 12, fontFamily: F.sb },
    linkCount: { color: P.ink2, fontSize: 15, fontFamily: F.xb, fontVariant: ['tabular-nums'] },
    dot: { width: 9, height: 9, borderRadius: 5 },
    // AppSummary
    boxes: { flexDirection: 'row', gap: 10 },
    // details
    overlay: { position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: 'rgba(3,5,9,0.55)', justifyContent: 'flex-end' },
    details: { maxHeight: '82%', backgroundColor: P.sheet, borderTopLeftRadius: 30, borderTopRightRadius: 30, paddingHorizontal: 20, paddingTop: 10, gap: 12 },
    grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
    statBox: { flexGrow: 1, flexBasis: '30%', backgroundColor: P.surface2, borderRadius: 18, padding: 12, gap: 2 },
    statBoxValue: { color: P.ink, fontSize: 20, fontFamily: F.xb, letterSpacing: -0.4, fontVariant: ['tabular-nums'] },
    box: { backgroundColor: P.surface2, borderRadius: 18, padding: 14, gap: 6 },
    label: { color: P.ink3, fontSize: 12, fontFamily: F.b, marginBottom: 6 },
    value: { color: P.ink, fontSize: 16, fontFamily: F.b, fontVariant: ['tabular-nums'] },
    hostRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
    input: { flex: 1, color: P.ink, fontSize: 18, fontFamily: F.sb, backgroundColor: P.sheet, borderColor: P.line, borderWidth: 1, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, fontVariant: ['tabular-nums'] },
    port: { color: P.ink2, fontSize: 16, fontFamily: F.sb },
    calBtn: { height: 44, paddingHorizontal: 16, borderRadius: 12, backgroundColor: P.accent, alignItems: 'center', justifyContent: 'center' },
    calBtnText: { color: P.onAccent, fontSize: 15, fontFamily: F.xb },
    foot: { color: P.ink3, fontSize: 12, textAlign: 'center', fontFamily: F.m },
  });
}
