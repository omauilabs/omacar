"use client";
import { useEffect, useRef, useState } from "react";
import {
  Car,
  BatteryCharging,
  Activity,
  Smartphone,
  Settings2,
  Sun,
  Moon,
  Zap,
  Gauge,
  Thermometer,
  Bluetooth,
  ArrowUpRight,
  Maximize,
  Pause,
  Play,
  Radio,
  Upload,
  Cable,
  CircleDot,
  Wind,
} from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import CarPlayPreview from "./carplay-preview";
import EnergyView from "./energy-view";
import AppearancePicker from "./appearance-picker";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { DRIVE_MODE_COPY, readDriveMode, type DriveMode } from "@/lib/drive-mode";
import { advanceDemo, type DemoScene } from "@/lib/demo-telemetry";
import { signalSegments, useTelemetryHistory, type TelemetrySample } from "@/lib/telemetry-history";

type Reading = {
  speed: number | null;
  rpm: number | null;
  coolant: number | null;
  voltage: number | null;
  soc: number | null;
  packVoltage: number | null;
  packTemp: number | null;
  power: number | null;
};
const blank: Reading = {
  speed: null,
  rpm: null,
  coolant: null,
  voltage: null,
  soc: null,
  packVoltage: null,
  packTemp: null,
  power: null,
};
const initial: Reading = {
  speed: 62,
  rpm: 2240,
  coolant: 88,
  voltage: 13.9,
  soc: 74,
  packVoltage: 151.2,
  packTemp: 31,
  power: 8.4,
};
const fmt = (n: number | null, d = 0) => (n === null ? "—" : n.toFixed(d));
function Spark({
  samples,
  signal,
  color = "var(--mint)",
}: {
  samples: TelemetrySample[];
  signal: keyof Reading;
  color?: string;
}) {
  const segments = signalSegments(samples, signal),
    values = segments.flat().map((p) => p.value);
  if (!values.length) return <div className="spark spark-empty">Waiting for readings</div>;
  const span = {
    speed: 20,
    rpm: 1000,
    coolant: 5,
    voltage: 2,
    soc: 5,
    packVoltage: 5,
    packTemp: 5,
    power: 10,
  }[signal];
  const min = Math.min(...values),
    max = Math.max(...values),
    middle = (min + max) / 2,
    range = Math.max(span, max - min),
    last = samples.at(-1)!.at;
  return (
    <svg className="spark" viewBox="0 0 260 50" preserveAspectRatio="none" aria-hidden="true">
      <line x1="0" y1="40" x2="260" y2="40" stroke="var(--border)" opacity=".5" />
      {segments.map((segment, i) => (
        <polyline
          key={i}
          points={segment
            .map(
              (p) =>
                `${260 - ((last - p.at) / 60000) * 260},${25 - ((p.value - middle) / range) * 32}`,
            )
            .join(" ")}
          fill="none"
          stroke={color}
          strokeWidth="2"
        />
      ))}
    </svg>
  );
}
export default function Home() {
  const [tab, setTab] = useState("drive"),
    [theme, setTheme] = useState("night"),
    [demo, setDemo] = useState(true),
    [paused, setPaused] = useState(false),
    [data, setData] = useState<Reading>(initial),
    [status, setStatus] = useState("Demo · simulated telemetry"),
    [time, setTime] = useState(""),
    [model, setModel] = useState(""),
    [modelError, setModelError] = useState(""),
    [units, setUnits] = useState("mph"),
    [focus, setFocus] = useState(false),
    [scene, setScene] = useState<DemoScene>("cruise"),
    [brightness, setBrightness] = useState(100),
    [gentle, setGentle] = useState(false),
    [prefsReady, setPrefsReady] = useState(false),
    [driveMode, setDriveMode] = useState<DriveMode>("normal"),
    [liveDriveMode, setLiveDriveMode] = useState<DriveMode | null>(null),
    [modeNotice, setModeNotice] = useState<DriveMode | null>(null);
  const history = useTelemetryHistory(data, demo, paused);
  useEffect(() => {
    if (!modeNotice) return;
    const timeout = setTimeout(() => setModeNotice(null), 2600);
    return () => clearTimeout(timeout);
  }, [modeNotice]);
  useEffect(() => {
    const context = (
      document as Document & {
        modelContext?: {
          registerTool: (tool: unknown, options: unknown) => void;
        };
      }
    ).modelContext;
    if (!context) return;
    const lifecycle = new AbortController();
    try {
      context.registerTool(
        {
          name: "configure_cockpit_theme",
          description: "Set the cockpit appearance to day, night, deep night, or clock-based auto.",
          inputSchema: {
            type: "object",
            properties: {
              theme: { type: "string", enum: ["day", "night", "deep", "auto"] },
            },
            required: ["theme"],
            additionalProperties: false,
          },
          annotations: { readOnlyHint: false },
          execute: async (input: unknown) => {
            const value = (input as { theme?: string })?.theme;
            if (!value || !["day", "night", "deep", "auto"].includes(value))
              throw new Error("Invalid theme");
            setTheme(value);
            return { theme: value };
          },
        },
        { signal: lifecycle.signal },
      );
    } catch {}
    return () => lifecycle.abort();
  }, []);
  const file = useRef<HTMLInputElement>(null),
    modelUrl = useRef(""),
    sceneRun = useRef({ scene: "cruise", seconds: 0, entrySpeed: 62 }),
    latestData = useRef(data);
  useEffect(() => {
    latestData.current = data;
  }, [data]);
  useEffect(() => {
    try {
      const saved = localStorage.getItem("crz-theme");
      if (saved && ["day", "night", "deep", "auto"].includes(saved)) setTheme(saved);
      const dim = Number(localStorage.getItem("crz-brightness") || 100);
      if (Number.isFinite(dim)) setBrightness(Math.max(60, Math.min(100, dim)));
      setGentle(localStorage.getItem("crz-gentle") === "true");
      const unit = localStorage.getItem("crz-units");
      if (unit === "mph" || unit === "km/h") setUnits(unit);
    } catch {}
    setPrefsReady(true);
    setTime(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
    const timer = setInterval(
      () =>
        setTime(
          new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          }),
        ),
      1000,
    );
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!prefsReady) return;
    try {
      localStorage.setItem("crz-theme", theme);
      localStorage.setItem("crz-brightness", String(brightness));
      localStorage.setItem("crz-gentle", String(gentle));
      localStorage.setItem("crz-units", units);
    } catch {}
  }, [theme, brightness, gentle, units, prefsReady]);
  useEffect(() => {
    if (!demo) return;
    setStatus("Demo · simulated telemetry");
    const timer = setInterval(() => {
      if (paused) return;
      if (sceneRun.current.scene !== scene)
        sceneRun.current = {
          scene,
          seconds: 0,
          entrySpeed: latestData.current.speed ?? 62,
        };
      sceneRun.current.seconds += 0.1;
      const { seconds, entrySpeed } = sceneRun.current;
      setData((old) => advanceDemo(old, scene, driveMode, seconds, entrySpeed));
    }, 100);
    return () => clearInterval(timer);
  }, [demo, paused, scene, driveMode]);
  useEffect(() => {
    if (demo) return;
    setData(blank);
    setLiveDriveMode(null);
    setStatus("OBD2 · waiting for local bridge");
    let last = 0;
    let socket: WebSocket;
    try {
      socket = new WebSocket("ws://127.0.0.1:8765");
      socket.onmessage = (e) => {
        try {
          const packet = JSON.parse(e.data);
          if (packet.source !== "obd") return;
          const next = { ...blank };
          for (const key of Object.keys(blank) as (keyof Reading)[])
            if (typeof packet[key] === "number" && Number.isFinite(packet[key]))
              next[key] = packet[key];
          last = Date.now();
          setData(next);
          setLiveDriveMode(readDriveMode(packet.driveMode));
          setStatus("OBD2 · live vehicle data");
        } catch {
          setStatus("OBD2 · invalid bridge data");
        }
      };
      socket.onerror = () => {
        setLiveDriveMode(null);
        setStatus("OBD2 · bridge unavailable");
      };
      socket.onclose = () => {
        setData(blank);
        setLiveDriveMode(null);
        setStatus("OBD2 · disconnected");
      };
    } catch {
      setStatus("OBD2 · connection unavailable");
    }
    const timer = setInterval(() => {
      if (last && Date.now() - last > 3000) {
        setData(blank);
        setLiveDriveMode(null);
        setStatus("OBD2 · stale data");
      }
    }, 1000);
    return () => {
      clearInterval(timer);
      if (socket) {
        socket.onmessage = null;
        socket.onclose = null;
        socket.onerror = null;
        socket.close();
      }
    };
  }, [demo]);
  useEffect(
    () => () => {
      if (modelUrl.current) URL.revokeObjectURL(modelUrl.current);
    },
    [],
  );
  const changeModel = async (f: File | undefined) => {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".glb")) {
      setModelError("Choose a self-contained .glb model.");
      return;
    }
    if (f.size > 80 * 1024 * 1024) {
      setModelError("Choose a model smaller than 80 MB.");
      return;
    }
    try {
      await import("@google/model-viewer");
      if (modelUrl.current) URL.revokeObjectURL(modelUrl.current);
      modelUrl.current = URL.createObjectURL(f);
      setModel(modelUrl.current);
      setModelError("");
    } catch {
      setModelError("The 3D viewer could not load.");
    }
  };
  const activeMode = demo ? driveMode : liveDriveMode;
  const modeCopy = activeMode ? DRIVE_MODE_COPY[activeMode] : null;
  const power = data.power ?? 0,
    charging = power < 0,
    efficientPreview =
      demo && activeMode !== "sport" && data.power !== null && Math.abs(data.power) < 7,
    night =
      theme === "night" ||
      theme === "deep" ||
      (theme === "auto" && (new Date().getHours() < 7 || new Date().getHours() >= 19));
  return (
    <main
      className={`cockpit ${night ? "night" : "day"} ${focus ? "focused" : ""} ${paused ? "paused" : ""} ${theme === "deep" ? "deep" : ""} ${gentle ? "gentle" : ""}`}
      data-view={tab}
      data-drive-mode={activeMode ?? "unknown"}
      style={
        {
          filter: `brightness(${brightness / 100})`,
          "--gauge-color":
            activeMode === "sport"
              ? "var(--mode-color)"
              : efficientPreview
                ? "var(--econ-meter)"
                : demo
                  ? "var(--normal-meter)"
                  : "var(--mode-color)",
        } as React.CSSProperties
      }
    >
      <header>
        <div className="brand">
          <span className="brandmark">ō</span>
          <span>
            omarchy <small> / DRIVE</small>
          </span>
        </div>
        <div className="top-car">
          HONDA <b>CR-Z</b> <span>2015</span>
        </div>
        <div className="top-right">
          <span
            className="header-drive-mode"
            aria-label={`Current ${demo ? "preview " : ""}drive mode: ${modeCopy?.title ?? "unknown"}`}
          >
            {modeCopy?.title ?? "MODE UNKNOWN"}
          </span>
          <span className="source">
            <i className={demo ? "amber" : ""} />
            {demo ? "DEMO" : "OBD2"}
          </span>
          <span>{time || "—"}</span>
          <button
            aria-label="Toggle day and night"
            onClick={() => setTheme(night ? "day" : "night")}
          >
            {night ? <Sun /> : <Moon />}
          </button>
          <button
            aria-label="Fullscreen"
            onClick={() => {
              if (!document.fullscreenElement)
                document.documentElement.requestFullscreen?.().catch(() => {});
              else document.exitFullscreen?.();
            }}
          >
            <Maximize />
          </button>
        </div>
      </header>
      <Tabs
        value={tab}
        onValueChange={(v) => setTab(String(v))}
        orientation="horizontal"
        className="shell"
      >
        <TabsList className="rail" aria-label="Dashboard views">
          {[
            ["drive", Car, "Drive"],
            ["energy", BatteryCharging, "Energy"],
            ["systems", Activity, "Systems"],
            ["carplay", Smartphone, "CarPlay"],
            ["setup", Settings2, "Display"],
          ].map(([id, Icon, label]) => {
            const I = Icon as typeof Car;
            return (
              <TabsTrigger key={String(id)} value={String(id)} className="rail-tab">
                <I />
                <span>{String(label)}</span>
              </TabsTrigger>
            );
          })}
          <div className="rail-bottom">
            <span>IMA</span>
            <i />
          </div>
        </TabsList>
        <TabsContent className="workspace" value={tab} aria-label={tab}>
          <div className="view-title">
            <div>
              <p className="eyebrow">
                {tab === "drive"
                  ? "HONDA CR-Z"
                  : tab === "energy"
                    ? "INTEGRATED MOTOR ASSIST"
                    : tab === "systems"
                      ? "VEHICLE TELEMETRY"
                      : tab === "carplay"
                        ? "IPHONE 16 PRO MAX"
                        : "COCKPIT PREFERENCES"}
              </p>
              <h1>
                {
                  {
                    drive: "Drive",
                    energy: "Energy",
                    systems: "Vehicle systems",
                    carplay: "CarPlay",
                    setup: "Display & controls",
                  }[tab]
                }
              </h1>
            </div>
            <div className="status">
              <i className={demo ? "amber" : ""} />
              {status}
            </div>
          </div>
          {tab === "drive" && (
            <>
              <div className="drive-toolbar">
                <div className="mode-switcher">
                  <span>{demo ? "PREVIEW DRIVE MODE" : "VEHICLE DRIVE MODE"}</span>
                  <Tabs
                    value={activeMode ?? "unknown"}
                    onValueChange={(v) => {
                      if (!demo) return;
                      const next = readDriveMode(v);
                      if (next && next !== driveMode) {
                        setDriveMode(next);
                        setModeNotice(next);
                      }
                    }}
                  >
                    <TabsList className="drive-mode-tabs" aria-label="Preview CR-Z drive mode">
                      {(["econ", "normal", "sport"] as DriveMode[]).map((mode) => (
                        <TabsTrigger key={mode} value={mode} disabled={!demo} data-mode={mode}>
                          {DRIVE_MODE_COPY[mode].title}
                        </TabsTrigger>
                      ))}
                    </TabsList>
                  </Tabs>
                </div>
                <div className="scenario-controls">
                  <Tabs
                    value={scene}
                    onValueChange={(v) => {
                      if (["cruise", "regen", "parked"].includes(String(v)))
                        setScene(v as DemoScene);
                      setPaused(false);
                    }}
                  >
                    <TabsList className="scenario-tabs" aria-label="Demo driving scenario">
                      <TabsTrigger value="cruise" disabled={!demo}>
                        Cruise
                      </TabsTrigger>
                      <TabsTrigger value="regen" disabled={!demo}>
                        Regenerate
                      </TabsTrigger>
                      <TabsTrigger value="parked" disabled={!demo}>
                        Parked
                      </TabsTrigger>
                    </TabsList>
                  </Tabs>
                  <button
                    className="focus-toggle"
                    aria-pressed={focus}
                    onClick={() => setFocus(!focus)}
                  >
                    <Maximize size={15} />
                    {focus ? "Full cockpit" : "Focus view"}
                  </button>
                </div>
              </div>
              <div className="drive-grid">
                <section className="speed-panel">
                  <div className="instrument-halo">
                    <svg viewBox="0 0 240 240" aria-hidden="true">
                      <circle cx="120" cy="120" r="111" />
                      <path d="M41.5 198.5 A111 111 0 1 1 198.5 198.5" />
                    </svg>
                    <div className="speed">
                      {fmt(
                        data.speed === null
                          ? null
                          : units === "mph"
                            ? data.speed
                            : data.speed * 1.60934,
                      )}
                    </div>
                    <button
                      className="unit"
                      onClick={() => setUnits(units === "mph" ? "km/h" : "mph")}
                    >
                      {units} <span>⌄</span>
                    </button>
                    <div className="active-drive-mode" role="status" aria-live="polite">
                      {modeCopy?.title ?? "UNKNOWN"}
                    </div>
                  </div>
                  <div className="speed-line" />
                  <div className="rpm">
                    <span>{fmt(data.rpm)}</span>
                    <small>RPM</small>
                  </div>
                  <div className="rpm-meter" aria-hidden="true">
                    <span
                      style={{
                        width: `${Math.min(100, (data.rpm ?? 0) / 70)}%`,
                      }}
                    />
                  </div>
                  <div
                    className="ima-balance"
                    aria-label={
                      data.power === null
                        ? "IMA power unknown"
                        : `IMA ${charging ? "charge" : "assist"} ${Math.abs(power).toFixed(1)} kilowatts`
                    }
                  >
                    <div className="ima-balance-labels">
                      <span>CHRG</span>
                      <span>ASST</span>
                    </div>
                    <div className="ima-balance-track">
                      <div className="charge-half">
                        {Array.from({ length: 6 }, (_, i) => (
                          <i
                            key={i}
                            className={data.power !== null && power < -(5 - i) * 2.2 ? "on" : ""}
                          />
                        ))}
                      </div>
                      <b />
                      <div className="assist-half">
                        {Array.from({ length: 6 }, (_, i) => (
                          <i
                            key={i}
                            className={data.power !== null && power > i * 2.2 ? "on" : ""}
                          />
                        ))}
                      </div>
                    </div>
                  </div>
                  <div className={`drive-mode ${charging ? "recovering" : ""}`}>
                    <CircleDot size={15} />{" "}
                    {data.power === null
                      ? "NO SIGNAL"
                      : Math.abs(power) < 0.05
                        ? "STANDBY"
                        : charging
                          ? "REGEN"
                          : "ASSIST"}
                  </div>
                  <p className="minor">
                    {demo
                      ? modeCopy?.detail
                      : "Mode signal " + (activeMode ? "received" : "unavailable")}
                  </p>
                </section>
                <section className="vehicle-stage">
                  {modeNotice && (
                    <div className="mode-notice" key={modeNotice} aria-hidden="true">
                      <CircleDot size={16} />
                      <div>
                        <strong>{DRIVE_MODE_COPY[modeNotice].title} selected</strong>
                        <span>{DRIVE_MODE_COPY[modeNotice].detail}</span>
                      </div>
                    </div>
                  )}
                  <div className="vehicle-name">
                    <span>CR–Z</span>
                    <small>2015 · IMA HYBRID</small>
                  </div>
                  {model ? (
                    <div className="model-wrap">
                      {/* model-viewer custom element, loaded only on import */}
                      {requireModel(model, gentle || paused)}
                    </div>
                  ) : (
                    <div className="car-photo concept">
                      <img
                        src="/crz-concept.png"
                        alt="AI-generated gunmetal Honda CR-Z concept with black rims, not a scan or live 3D model"
                      />
                    </div>
                  )}
                  <div className="stage-bottom">
                    <span>
                      <i /> {model ? "3D model · drag to orbit" : "CR-Z studio concept"}
                    </span>
                    <button onClick={() => setTab("energy")}>
                      <Zap size={15} /> Explore energy
                    </button>
                  </div>
                </section>
                <section className="battery-card">
                  <div className="card-label">
                    <BatteryCharging />
                    <span>Hybrid battery</span>
                    <button
                      className="battery-open"
                      aria-label="Open hybrid battery details"
                      onClick={() => setTab("energy")}
                    >
                      <ArrowUpRight size={17} />
                    </button>
                  </div>
                  <div className="soc">
                    {fmt(data.soc)}
                    <span>%</span>
                  </div>
                  <div className="battery-bars">
                    {Array.from({ length: 20 }, (_, i) => (
                      <i
                        key={i}
                        className={data.soc !== null && i < data.soc / 5 ? "filled" : ""}
                      />
                    ))}
                  </div>
                  <p className="battery-caption">
                    {data.soc === null ? "Charge reading unavailable" : "State of charge"}
                  </p>
                  <div className="battery-details">
                    <div>
                      <span>
                        {fmt(data.packVoltage, 1)} <small>V</small>
                      </span>
                      <label>Pack voltage</label>
                    </div>
                    <div>
                      <span>
                        {fmt(data.packTemp)}
                        <small> °C</small>
                      </span>
                      <label>Temperature</label>
                    </div>
                  </div>
                  {demo && <Spark samples={history} signal="soc" />}
                  <div className="tiny">
                    {demo ? "SIMULATED IMA READINGS" : "HONDA IMA PROFILE REQUIRED"}
                  </div>
                </section>
              </div>
              <div className="lower-grid">
                <section className="panel flow-card">
                  <div className="section-label">
                    <Zap size={17} /> ENERGY FLOW{" "}
                    <span>
                      {data.power === null
                        ? "Unavailable"
                        : power === 0
                          ? "At rest"
                          : charging
                            ? "Regenerating"
                            : "Motor assist"}
                    </span>
                  </div>
                  <div
                    className={`energy-line ${charging ? "reverse" : ""} ${power === 0 ? "resting" : ""}`}
                  >
                    <div>
                      <BatteryCharging />
                      <span>IMA BATTERY</span>
                    </div>
                    <div className="flow-track">
                      <i />
                      <i />
                      <i />
                    </div>
                    <strong>
                      {fmt(data.power === null ? null : Math.abs(power), 1)}
                      <small> kW</small>
                    </strong>
                    <div className="flow-track">
                      <i />
                      <i />
                      <i />
                    </div>
                    <div>
                      <Gauge />
                      <span>DRIVETRAIN</span>
                    </div>
                  </div>
                </section>
                <section className="panel mini-metrics">
                  <div>
                    <Thermometer />
                    <strong>
                      {fmt(data.coolant)}
                      <small> °C</small>
                    </strong>
                    <span>Coolant</span>
                  </div>
                  <div>
                    <Zap />
                    <strong>
                      {fmt(data.voltage, 1)}
                      <small> V</small>
                    </strong>
                    <span>12 V system</span>
                  </div>
                </section>
                <button className="panel phone-card" onClick={() => setTab("carplay")}>
                  <div className="phone-icon">
                    <Smartphone />
                  </div>
                  <div>
                    <strong>iPhone 16 Pro Max</strong>
                    <span>
                      Explore CarPlay <ArrowUpRight size={14} />
                    </span>
                  </div>
                </button>
              </div>
            </>
          )}
          <div hidden={tab !== "energy"}>
            <EnergyView data={data} demo={demo} paused={paused} history={history} />
          </div>
          {tab === "systems" && (
            <div className="systems-grid">
              {[
                [
                  "Road speed",
                  data.speed === null ? null : units === "mph" ? data.speed : data.speed * 1.60934,
                  units,
                  Gauge,
                ],
                ["Engine speed", data.rpm, "rpm", Activity],
                ["Coolant temperature", data.coolant, "°C", Thermometer],
                ["Adapter voltage", data.voltage, "V", Zap],
                ["IMA charge", data.soc, "%", BatteryCharging],
                ["IMA temperature", data.packTemp, "°C", Wind],
              ].map(([label, value, unit, Icon], i) => {
                const I = Icon as typeof Car;
                return (
                  <section className="panel system-card" key={String(label)}>
                    <div className="section-label">
                      <I />
                      {String(label)}
                    </div>
                    <div className="stat-large">
                      {fmt(value as number | null, i === 3 ? 1 : 0)}
                      <small>{String(unit)}</small>
                    </div>
                    {demo ? (
                      <Spark
                        samples={history}
                        signal={
                          (["speed", "rpm", "coolant", "voltage", "soc", "packTemp"] as const)[i]
                        }
                      />
                    ) : (
                      <p className="minor">
                        {value === null
                          ? "Unsupported or no fresh reading"
                          : "Fresh vehicle reading"}
                      </p>
                    )}
                    <span className="minor">{demo ? "Simulated signal" : "Read-only OBD2"}</span>
                  </section>
                );
              })}
            </div>
          )}
          <div hidden={tab !== "carplay"}>
            <CarPlayPreview />
          </div>
          {tab === "setup" && (
            <div className="setup-grid">
              <section className="panel">
                <h2>Appearance</h2>
                <p>Choose the light that feels right.</p>
                <AppearancePicker value={theme} onChange={setTheme} />
                <p className="minor">
                  Auto follows your tablet clock. Deep night uses warm accents on black.
                </p>
                <div className="dimmer-control">
                  <div>
                    <label id="screen-dimmer">Screen dimmer</label>
                    <output>{brightness}%</output>
                  </div>
                  <Slider
                    aria-labelledby="screen-dimmer"
                    value={[brightness]}
                    min={60}
                    max={100}
                    step={5}
                    onValueChange={(v) => setBrightness(Array.isArray(v) ? v[0] : v)}
                    className="screen-dimmer"
                  />
                  <p className="minor">
                    Dims this interface; your tablet brightness stays unchanged.
                  </p>
                </div>
                <div className="preference-row">
                  <div>
                    <label htmlFor="gentle-motion">Gentle motion</label>
                    <p className="minor">Quiet transitions and energy-flow effects.</p>
                  </div>
                  <Switch id="gentle-motion" checked={gentle} onCheckedChange={setGentle} />
                </div>
                <h2>Telemetry source</h2>
                <button
                  className="primary-button"
                  onClick={() => {
                    setData(demo ? blank : initial);
                    setLiveDriveMode(null);
                    setModeNotice(null);
                    setPaused(false);
                    sceneRun.current = { scene, seconds: 0, entrySpeed: 62 };
                    setDemo(!demo);
                  }}
                >
                  {demo ? "Connect local OBD2 bridge" : "Return to demo"}
                </button>
                <p className="minor">
                  Live mode clears demo readings. Unknown signals stay blank; readings expire after
                  3 seconds.
                </p>
                <h2>Driving view</h2>
                <button
                  className="outline-button"
                  onClick={() => {
                    setFocus(!focus);
                    setTab("drive");
                  }}
                >
                  {focus ? "Show full cockpit" : "Focus on speed and battery"}
                </button>
              </section>
              <section className="panel">
                <h2>Your CR-Z</h2>
                <p>2015 · Gunmetal gray · Black stock rims</p>
                <button className="outline-button" onClick={() => file.current?.click()}>
                  <Upload />
                  Import a 3D model
                </button>
                <p className="minor">
                  A self-contained 3D model enables touch orbit and zoom. The centerpiece is an
                  AI-generated studio concept. Import a model here for true 3D orbit and zoom.
                </p>
                <a
                  href="https://www.viz-people.com/portfolio/free-3d-model-honda-cr-z/"
                  target="_blank"
                  rel="noreferrer"
                >
                  Free CR-Z model source ↗
                </a>
                <h2>3-Mode Drive System</h2>
                <p className="minor">
                  ECON, NORMAL, and SPORT are separate from motor assist and regeneration. The
                  selector previews the display; it does not change your car’s mode. Live mode
                  requires a verified Honda mode signal.
                </p>
                <h2>Hardware</h2>
                <dl>
                  <dt>Computer</dt>
                  <dd>Surface Pro 7 · Intel · Omarchy</dd>
                  <dt>CarPlay</dt>
                  <dd>CPC200-CCPA · local host required</dd>
                  <dt>OBD2</dt>
                  <dd>OBDLink EX · USB serial</dd>
                  <dt>Battery detail</dt>
                  <dd>Honda IMA profile not yet verified</dd>
                </dl>
                <p className="minor">
                  Photo:{" "}
                  <a
                    href="https://commons.wikimedia.org/wiki/File:2014_Honda_CR-Z_Sport-T_i-VTEC_1.5_Front.jpg"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Vauxford / Wikimedia Commons
                  </a>{" "}
                  ·{" "}
                  <a
                    href="https://creativecommons.org/licenses/by-sa/4.0/"
                    target="_blank"
                    rel="noreferrer"
                  >
                    CC BY-SA 4.0
                  </a>
                  . 2014 reference, cropped and desaturated for display.
                </p>
              </section>
            </div>
          )}
          <input
            ref={file}
            type="file"
            accept=".glb"
            hidden
            onChange={(e) => void changeModel(e.target.files?.[0])}
          />
          {modelError && <p role="alert">{modelError}</p>}
        </TabsContent>
      </Tabs>
      <footer>
        <span>
          <i className={demo ? "amber" : ""} />
          {demo ? "DEMO MODE · NOT VEHICLE DATA" : status}
        </span>
        <div>
          {demo && (
            <button onClick={() => setPaused(!paused)}>
              {paused ? <Play size={15} /> : <Pause size={15} />} {paused ? "Resume" : "Pause"}{" "}
              simulation
            </button>
          )}
          <span>
            OMARCHY <b>·</b> CR-Z
          </span>
        </div>
      </footer>
    </main>
  );
}
function requireModel(src: string, still: boolean) {
  return (
    <model-viewer
      src={src}
      camera-controls
      auto-rotate={!still}
      shadow-intensity="1.4"
      exposure="1"
      environment-image="neutral"
      alt="User-loaded Honda CR-Z 3D model"
      style={{ width: "100%", height: "100%" }}
    />
  );
}
declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "model-viewer": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        src: string;
        "camera-controls"?: boolean;
        "auto-rotate"?: boolean;
        "shadow-intensity"?: string;
        exposure?: string;
        "environment-image"?: string;
        alt?: string;
      };
    }
  }
}
