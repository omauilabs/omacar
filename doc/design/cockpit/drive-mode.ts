export type DriveMode = 'econ' | 'normal' | 'sport';
export function readDriveMode(value: unknown): DriveMode | null {
  if (typeof value !== 'string') return null;
  const normalized = value.toLowerCase();
  return normalized === 'econ' || normalized === 'normal' || normalized === 'sport' ? normalized : null;
}
export const DRIVE_MODE_COPY = {
  econ: { title: 'ECON', detail: 'Efficiency prioritized', description: 'Prioritizes fuel economy over engine response and climate control.' },
  normal: { title: 'NORMAL', detail: 'Balanced response', description: 'Balances driving performance with fuel efficiency.' },
  sport: { title: 'SPORT', detail: 'Sharper response', description: 'Prioritizes a more responsive driving experience.' },
} as const;
