import type { WeatherInfo } from '../lib/types';
import { Wind } from 'lucide-react';

export function WindCompass({ weather }: { weather: WeatherInfo }) {
  const windDeg = weather.wind_from_deg || 0;
  const straightDeg = weather.home_straight_bearing || 0;

  const effectColor = weather.effect.includes('Headwind')
    ? 'text-amber-300 bg-amber-500/15 border-amber-500/30'
    : weather.effect.includes('Tailwind')
    ? 'text-emerald-300 bg-emerald-500/15 border-emerald-500/30'
    : 'text-sky-300 bg-sky-500/15 border-sky-500/30';

  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/70 px-3.5 py-2.5">
      <div className="relative h-12 w-12 shrink-0">
        <svg viewBox="0 0 64 64" className="h-12 w-12">
          <circle cx="32" cy="32" r="28" fill="none" stroke="#334155" strokeWidth="1.5" />
          <text x="32" y="10" textAnchor="middle" className="fill-slate-500 text-[7px] font-semibold">N</text>
          {/* Home straight arrow (emerald) */}
          <g transform={`rotate(${straightDeg} 32 32)`}>
            <line x1="32" y1="48" x2="32" y2="16" stroke="#10b981" strokeWidth="2.2" strokeDasharray="3 2" />
            <polygon points="32,12 28.5,19 35.5,19" fill="#10b981" />
          </g>
          {/* Wind blowing FROM arrow (sky/amber) */}
          <g transform={`rotate(${(windDeg + 180) % 360} 32 32)`}>
            <line x1="32" y1="50" x2="32" y2="18" stroke="#38bdf8" strokeWidth="2" />
            <polygon points="32,14 29,21 35,21" fill="#38bdf8" />
          </g>
          <circle cx="32" cy="32" r="2.5" fill="#e2e8f0" />
        </svg>
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Wind className="h-3.5 w-3.5 text-sky-400" />
          <span className="text-xs font-semibold text-slate-200 tabular-nums">
            {weather.wind_speed_kmh} km/h {weather.wind_compass}
          </span>
          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${effectColor}`}>
            {weather.effect}
          </span>
        </div>
        <p className="mt-0.5 truncate text-[11px] text-slate-400" title={weather.pace_bias}>
          {weather.pace_bias}
        </p>
        <p className="text-[10px] text-slate-500">
          {weather.source} · {weather.temperature_c !== null ? `${weather.temperature_c}°C` : 'Temp N/A'} · Rain {weather.rain_chance_pct}% ({weather.rain_expected_mm}mm)
        </p>
      </div>
    </div>
  );
}
