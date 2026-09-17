'use client';

import { useId, useState } from 'react';
import {
  ArrowDownLeft,
  ArrowUpRight,
  BatteryCharging,
  CircleDot,
  Gauge,
  Thermometer,
  Zap,
} from 'lucide-react';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { signalSegments, type TelemetrySample } from '@/lib/telemetry-history';

type EnergyData = {
  soc: number | null;
  packVoltage: number | null;
  packTemp: number | null;
  power: number | null;
};
const value = (n: number | null, decimals = 0) =>
  n === null ? '—' : n.toFixed(decimals);

export default function EnergyView({
  data,
  demo,
  paused,
  history,
}: {
  data: EnergyData;
  demo: boolean;
  paused: boolean;
  history: TelemetrySample[];
}) {
  const [view, setView] = useState('charge');
  const [windowSeconds, setWindowSeconds] = useState(60);
  const clipId = useId().replace(/:/g, '');
  const recovering = (data.power ?? 0) < -0.05;
  const resting = data.power !== null && Math.abs(data.power) < 0.05;
  const state =
    data.power === null
      ? 'No reading'
      : resting
        ? 'At rest'
        : recovering
          ? 'Regenerating'
          : 'Motor assist';
  const color =
    data.power === null || resting
      ? 'var(--muted)'
      : recovering
        ? 'var(--mint)'
        : 'var(--mode-color)';
  const latestAt = history.at(-1)?.at ?? 0;
  const recent = history.filter(
    (sample) => latestAt - sample.at <= windowSeconds * 1000,
  );
  const segments = signalSegments(recent, 'power');
  const powers = recent.flatMap((sample) =>
    sample.power === null ? [] : [sample.power],
  );
  const maxAssist = powers.length ? Math.max(0, ...powers) : null;
  const maxRecovery = powers.length
    ? Math.max(0, ...powers.map((power) => -power))
    : null;
  const range = Math.max(
    15,
    Math.ceil(Math.max(0, ...powers.map(Math.abs)) / 5) * 5,
  );
  const x = (at: number) =>
    640 - ((latestAt - at) / (windowSeconds * 1000)) * 640;
  const y = (power: number) => 90 - (power / range) * 78;
  const latest = recent.at(-1);
  const samplesAvailable = powers.length > 1;

  return (
    <div className="energy-studio">
      <section className="energy-main">
        <div className="energy-main-top">
          <div>
            <span className="eyebrow">INTEGRATED MOTOR ASSIST</span>
            <h2>Hybrid battery</h2>
          </div>
          <Tabs value={view} onValueChange={(v) => setView(String(v))}>
            <TabsList className="energy-tabs" aria-label="Battery display">
              <TabsTrigger value="charge">Charge</TabsTrigger>
              <TabsTrigger value="flow">Power</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        {view === 'charge' ? (
          <>
            <div className="charge-stage">
              <div className="charge-orbit">
                <svg
                  viewBox="0 0 300 300"
                  role="img"
                  aria-label={
                    data.soc === null
                      ? 'Battery charge unavailable'
                      : `State of charge ${value(data.soc)} percent`
                  }
                >
                  <circle cx="150" cy="150" r="123" className="orbit-track" />
                  <circle
                    cx="150"
                    cy="150"
                    r="123"
                    className="orbit-value"
                    strokeDasharray={`${Math.min(100, Math.max(0, data.soc ?? 0)) * 7.728} 772.8`}
                    transform="rotate(-90 150 150)"
                  />
                  {Array.from({ length: 40 }, (_, i) => (
                    <line
                      key={i}
                      x1="150"
                      y1="14"
                      x2="150"
                      y2="19"
                      transform={`rotate(${i * 9} 150 150)`}
                      stroke="var(--muted)"
                      opacity=".4"
                    />
                  ))}
                </svg>
                <div className="orbit-reading">
                  <BatteryCharging size={24} />
                  <strong>
                    {value(data.soc)}
                    <small>%</small>
                  </strong>
                  <span>STATE OF CHARGE</span>
                </div>
              </div>
              <div className="charge-story">
                <span className="energy-state" style={{ color }}>
                  {data.power === null || resting ? (
                    <CircleDot />
                  ) : recovering ? (
                    <ArrowDownLeft />
                  ) : (
                    <ArrowUpRight />
                  )}
                  {state}
                </span>
                <h3>
                  {data.power === null
                    ? 'Waiting for your CR-Z.'
                    : resting
                      ? 'Energy at rest.'
                      : recovering
                        ? 'Every slowdown gives back.'
                        : 'Electric power. On demand.'}
                </h3>
                <p>
                  {data.power === null
                    ? 'Battery readings appear when a supported IMA signal is available.'
                    : resting
                      ? 'No electric motor power is shown. The selected drive mode stays visible above.'
                      : recovering
                        ? 'As you slow down, the motor returns energy to the hybrid battery.'
                        : 'The hybrid battery supplies the motor to assist the gasoline engine.'}
                </p>
                <div className="energy-pill">
                  <Zap size={16} />
                  <strong style={{ color }}>
                    {value(
                      data.power === null ? null : Math.abs(data.power),
                      1,
                    )}{' '}
                    kW
                  </strong>
                  <span>
                    {data.power === null
                      ? 'power unavailable'
                      : resting
                        ? 'motor at rest'
                        : recovering
                          ? 'back to the battery'
                          : 'to the electric motor'}
                  </span>
                </div>
              </div>
            </div>
            <div
              className={`pack-power-path ${recovering ? 'is-recovering' : ''} ${resting || data.power === null ? 'is-idle' : ''}`}
              style={{ '--flow-color': color } as React.CSSProperties}
              aria-label={
                data.power === null
                  ? 'Energy flow unavailable'
                  : resting
                    ? 'No energy transfer'
                    : recovering
                      ? 'Electric motor to hybrid battery'
                      : 'Hybrid battery to electric motor'
              }
            >
              <div>
                <BatteryCharging />
                <span>Hybrid battery</span>
              </div>
              <div className="pack-transfer">
                <span />
                <span />
                <span />
                <b>
                  {resting || data.power === null
                    ? '—'
                    : recovering
                      ? '←'
                      : '→'}
                </b>
              </div>
              <div>
                <Gauge />
                <span>Electric motor</span>
              </div>
            </div>
          </>
        ) : (
          <div className="power-stage precision-power">
            <div className="power-summary-row">
              <div className="power-reading">
                <span className="eyebrow">ELECTRIC MOTOR POWER</span>
                <strong style={{ color }}>
                  {data.power !== null && data.power > 0 ? '+' : ''}
                  {value(data.power, 1)}
                  <small>kW</small>
                </strong>
                <span>{state}</span>
              </div>
              <Tabs
                value={String(windowSeconds)}
                onValueChange={(v) => setWindowSeconds(Number(v))}
              >
                <TabsList
                  className="history-window"
                  aria-label="Power history time window"
                >
                  <TabsTrigger value="30">30 s</TabsTrigger>
                  <TabsTrigger value="60">60 s</TabsTrigger>
                </TabsList>
              </Tabs>
            </div>
            <div className="power-plot">
              <div className="power-scale" aria-hidden="true">
                <span>+{range}</span>
                <span>0</span>
                <span>−{range}</span>
              </div>
              <div className="power-plot-area">
                <svg
                  className="power-history"
                  viewBox="0 0 640 180"
                  preserveAspectRatio="none"
                  role="img"
                  aria-label={`Motor power over the last ${windowSeconds} seconds. Above zero is assist, below zero is regeneration. ${samplesAvailable ? `Peak assist ${value(maxAssist, 1)} kilowatts, peak regeneration ${value(maxRecovery, 1)} kilowatts.` : 'Collecting readings.'}`}
                >
                  <defs>
                    <clipPath id={`${clipId}-assist`}>
                      <rect x="0" y="0" width="642" height="90" />
                    </clipPath>
                    <clipPath id={`${clipId}-regen`}>
                      <rect x="0" y="90" width="642" height="90" />
                    </clipPath>
                  </defs>
                  {[12, 90, 168].map((height) => (
                    <line
                      key={height}
                      x1="0"
                      y1={height}
                      x2="640"
                      y2={height}
                      stroke="var(--border)"
                      strokeDasharray={height === 90 ? undefined : '3 7'}
                    />
                  ))}
                  {[160, 320, 480].map((width) => (
                    <line
                      key={width}
                      x1={width}
                      y1="12"
                      x2={width}
                      y2="168"
                      stroke="var(--border)"
                      opacity=".35"
                    />
                  ))}
                  {segments
                    .filter((segment) => segment.length > 1)
                    .map((segment, index) => {
                      const points = segment
                        .map((point) => `${x(point.at)},${y(point.value)}`)
                        .join(' ');
                      const area = `M ${x(segment[0].at)} 90 L ${points.replaceAll(',', ' ')} L ${x(segment.at(-1)!.at)} 90 Z`;
                      return (
                        <g key={index}>
                          {(['assist', 'regen'] as const).map((kind) => (
                            <g key={kind} clipPath={`url(#${clipId}-${kind})`}>
                              <path
                                d={area}
                                fill={
                                  kind === 'assist'
                                    ? 'var(--mode-color)'
                                    : 'var(--mint)'
                                }
                                opacity=".1"
                              />
                              <polyline
                                points={points}
                                fill="none"
                                stroke={
                                  kind === 'assist'
                                    ? 'var(--mode-color)'
                                    : 'var(--mint)'
                                }
                                strokeWidth="2.5"
                                strokeLinejoin="round"
                                vectorEffect="non-scaling-stroke"
                              />
                            </g>
                          ))}
                        </g>
                      );
                    })}
                  {latest?.power !== null && latest?.power !== undefined && (
                    <circle
                      cx="637"
                      cy={y(latest.power)}
                      r="3"
                      fill={
                        latest.power < 0 ? 'var(--mint)' : 'var(--mode-color)'
                      }
                    />
                  )}
                </svg>
                {!samplesAvailable && (
                  <div className="history-empty">
                    {data.power === null
                      ? 'Waiting for motor power'
                      : 'Collecting your first readings…'}
                  </div>
                )}
                <div className="history-time">
                  <span>−{windowSeconds} s</span>
                  <span>−{windowSeconds / 2} s</span>
                  <span>Latest</span>
                </div>
              </div>
            </div>
            <div className="power-legend">
              <span>
                <i />
                Assist · above zero
              </span>
              <span>
                <i />
                Regeneration · below zero
              </span>
            </div>
            <div className="power-peaks">
              <div>
                <span>Peak assist</span>
                <strong>
                  {value(maxAssist, 1)}
                  <small> kW</small>
                </strong>
              </div>
              <div>
                <span>Peak regeneration</span>
                <strong>
                  {value(maxRecovery, 1)}
                  <small> kW</small>
                </strong>
              </div>
              <p>Observed in this {windowSeconds}-second window</p>
            </div>
          </div>
        )}
        <div className="energy-bottom">
          <span>
            <i />
            {demo
              ? 'Demo battery behavior'
              : data.power === null
                ? 'Waiting for vehicle input'
                : 'Vehicle input'}
          </span>
          <span>
            {demo && paused ? 'Simulation paused' : 'Recent readings only'}
          </span>
        </div>
      </section>
      <aside className="battery-instruments">
        {[
          {
            label: 'PACK VOLTAGE',
            number: data.packVoltage,
            unit: 'V',
            icon: Zap,
            description: 'High-voltage IMA system',
            decimal: 1,
          },
          {
            label: 'PACK TEMPERATURE',
            number: data.packTemp,
            unit: '°C',
            icon: Thermometer,
            description: 'Battery temperature',
            decimal: 0,
          },
        ].map(({ label, number, unit, icon: Icon, description, decimal }) => (
          <section className="instrument" key={label}>
            <div>
              <Icon size={19} />
              <span>{label}</span>
            </div>
            <strong>
              {value(number, decimal)}
              <small>{unit}</small>
            </strong>
            <p>{description}</p>
            <div className="instrument-foot">
              {number === null
                ? 'Reading unavailable'
                : demo
                  ? 'Simulated sensor'
                  : 'Vehicle reading'}
            </div>
          </section>
        ))}
        <section className="instrument pack-summary">
          <BatteryCharging size={22} />
          <h3>Honda IMA</h3>
          <p>
            2015 CR-Z
            <br />
            Lithium-ion hybrid system
          </p>
          <span>
            {demo
              ? 'Illustrative battery behavior'
              : 'Battery profile unverified'}
          </span>
        </section>
      </aside>
    </div>
  );
}
