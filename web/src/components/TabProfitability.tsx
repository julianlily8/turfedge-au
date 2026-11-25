import { useMemo, useState } from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from 'recharts';
import { SlidersHorizontal, TrendingUp, DollarSign, Target, Edit3, RotateCcw } from 'lucide-react';
import type { SettlementDoc } from '../lib/types';
import {
  computeProfitability,
  filterSettlements,
  loadOddsOverrides,
  overrideKey,
  saveOddsOverrides,
  type OddsOverrideMap,
  type ProfitabilityFilters,
} from '../lib/profitability';

interface Props {
  settlements: SettlementDoc[];
}

export function TabProfitability({ settlements }: Props) {
  const [filters, setFilters] = useState<ProfitabilityFilters>({
    trackCondition: 'All',
    distanceBand: 'All',
    flucDirection: 'All',
    tier: 'All',
  });
  const [overrides, setOverrides] = useState<OddsOverrideMap>(() => loadOddsOverrides());

  const filtered = useMemo(() => filterSettlements(settlements, filters), [settlements, filters]);
  const stats = useMemo(() => computeProfitability(filtered, overrides), [filtered, overrides]);

  const updateOverride = (settlementId: string, betKey: string, val: string) => {
    const key = overrideKey(settlementId, betKey);
    const next = { ...overrides };
    const num = Number(val);
    if (!val.trim() || Number.isNaN(num) || num <= 0) {
      delete next[key];
    } else {
      next[key] = num;
    }
    setOverrides(next);
    saveOddsOverrides(next);
  };

  const resetAllOverrides = () => {
    setOverrides({});
    saveOddsOverrides({});
  };

  const winAll = stats.categories.find((c) => c.key === 'win_all');
  const winOverlay = stats.categories.find((c) => c.key === 'win_overlay');

  return (
    <div className="space-y-5">
      {/* Weight-Tuning Filters Bar */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <SlidersHorizontal className="h-4 w-4 text-emerald-400" />
            <h2 className="text-sm font-bold text-white">
              Standardized $10 Unit Profitability &amp; Weight-Tuning Filters
            </h2>
          </div>
          <span className="text-xs text-slate-400">
            Showing <strong className="text-slate-100">{filtered.length}</strong> settled record(s) · SRM includes 0.75x correlation haircut
          </span>
        </div>

        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase text-slate-400">Track Condition</label>
            <select
              value={filters.trackCondition}
              onChange={(e) => setFilters({ ...filters, trackCondition: e.target.value })}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
            >
              {['All', 'Firm', 'Good', 'Soft', 'Heavy', 'Synthetic'].map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase text-slate-400">Distance Band</label>
            <select
              value={filters.distanceBand}
              onChange={(e) => setFilters({ ...filters, distanceBand: e.target.value })}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
            >
              <option value="All">All Distances</option>
              <option value="Sprint">Sprint (≤1200m)</option>
              <option value="Middle">Middle (1201–1600m)</option>
              <option value="Staying">Staying (&gt;1600m)</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase text-slate-400">Market Direction</label>
            <select
              value={filters.flucDirection}
              onChange={(e) => setFilters({ ...filters, flucDirection: e.target.value })}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
            >
              {['All', 'Firmer', 'Steady', 'Drifter'].map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase text-slate-400">Meeting Tier</label>
            <select
              value={filters.tier}
              onChange={(e) => setFilters({ ...filters, tier: e.target.value })}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
            >
              {['All', 'Metro', 'Provincial'].map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Top KPI Cards */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Combined Net P/L ($10 Units)</span>
            <DollarSign className="h-4 w-4 text-emerald-400" />
          </div>
          <div
            className={`mt-1 text-2xl font-black tabular-nums ${
              stats.overall.netProfit >= 0 ? 'text-emerald-400' : 'text-rose-400'
            }`}
          >
            {stats.overall.netProfit >= 0 ? '+' : ''}${stats.overall.netProfit.toFixed(2)}
          </div>
          <div className="mt-1 text-xs text-slate-400 tabular-nums">
            Staked ${stats.overall.totalStaked.toFixed(0)} · Return ${stats.overall.totalReturn.toFixed(2)}
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Combined POT%</span>
            <TrendingUp className="h-4 w-4 text-sky-400" />
          </div>
          <div
            className={`mt-1 text-2xl font-black tabular-nums ${
              stats.overall.potPct >= 0 ? 'text-emerald-400' : 'text-rose-400'
            }`}
          >
            {stats.overall.potPct >= 0 ? '+' : ''}
            {stats.overall.potPct.toFixed(1)}%
          </div>
          <div className="mt-1 text-xs text-slate-400 tabular-nums">
            {stats.overall.wins}/{stats.overall.bets} bets won ({stats.overall.strikeRatePct.toFixed(1)}% SR)
          </div>
        </div>

        <div className="rounded-2xl border border-emerald-500/30 bg-emerald-950/20 p-4">
          <div className="flex items-center justify-between text-xs text-emerald-300">
            <span>Win (Value Overlays Only)</span>
            <Target className="h-4 w-4 text-emerald-400" />
          </div>
          <div
            className={`mt-1 text-2xl font-black tabular-nums ${
              (winOverlay?.potPct || 0) >= 0 ? 'text-emerald-300' : 'text-rose-400'
            }`}
          >
            {(winOverlay?.potPct || 0) >= 0 ? '+' : ''}
            {(winOverlay?.potPct || 0).toFixed(1)}% POT
          </div>
          <div className="mt-1 text-xs text-slate-300 tabular-nums">
            Net {(winOverlay?.netProfit || 0) >= 0 ? '+' : ''}${(winOverlay?.netProfit || 0).toFixed(2)} · SR{' '}
            {(winOverlay?.strikeRatePct || 0).toFixed(1)}% ({winOverlay?.bets || 0} bets)
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Win (All #1 Selections)</span>
            <Target className="h-4 w-4 text-slate-400" />
          </div>
          <div
            className={`mt-1 text-2xl font-black tabular-nums ${
              (winAll?.potPct || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'
            }`}
          >
            {(winAll?.potPct || 0) >= 0 ? '+' : ''}
            {(winAll?.potPct || 0).toFixed(1)}% POT
          </div>
          <div className="mt-1 text-xs text-slate-400 tabular-nums">
            Net {(winAll?.netProfit || 0) >= 0 ? '+' : ''}${(winAll?.netProfit || 0).toFixed(2)} · SR{' '}
            {(winAll?.strikeRatePct || 0).toFixed(1)}% ({winAll?.bets || 0} bets)
          </div>
        </div>
      </div>

      {/* Category Breakdown Table */}
      <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/70">
        <div className="border-b border-slate-800 px-4 py-3">
          <h3 className="text-sm font-bold text-white">Profitability Breakdown by Bet Type ($10 Standardized Units)</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-800 bg-slate-950/60 text-[11px] uppercase tracking-wider text-slate-400">
              <tr>
                <th className="px-4 py-2.5">Bet Category</th>
                <th className="px-4 py-2.5 text-right">Bets</th>
                <th className="px-4 py-2.5 text-right">Wins</th>
                <th className="px-4 py-2.5 text-right">Strike Rate</th>
                <th className="px-4 py-2.5 text-right">Total Staked</th>
                <th className="px-4 py-2.5 text-right">Total Return</th>
                <th className="px-4 py-2.5 text-right">Net P/L</th>
                <th className="px-4 py-2.5 text-right">POT%</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70 tabular-nums">
              {stats.categories.map((c) => (
                <tr key={c.key} className="hover:bg-slate-800/40">
                  <td className="px-4 py-2.5 font-semibold text-slate-100">{c.label}</td>
                  <td className="px-4 py-2.5 text-right text-slate-300">{c.bets}</td>
                  <td className="px-4 py-2.5 text-right text-slate-300">{c.wins}</td>
                  <td className="px-4 py-2.5 text-right text-slate-300">{c.strikeRatePct.toFixed(1)}%</td>
                  <td className="px-4 py-2.5 text-right text-slate-300">${c.totalStaked.toFixed(2)}</td>
                  <td className="px-4 py-2.5 text-right text-slate-200">${c.totalReturn.toFixed(2)}</td>
                  <td
                    className={`px-4 py-2.5 text-right font-bold ${
                      c.netProfit > 0 ? 'text-emerald-400' : c.netProfit < 0 ? 'text-rose-400' : 'text-slate-400'
                    }`}
                  >
                    {c.netProfit >= 0 ? '+' : ''}${c.netProfit.toFixed(2)}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-bold ${
                        c.potPct > 0
                          ? 'bg-emerald-500/15 text-emerald-300'
                          : c.potPct < 0
                          ? 'bg-rose-500/15 text-rose-300'
                          : 'bg-slate-800 text-slate-400'
                      }`}
                    >
                      {c.potPct >= 0 ? '+' : ''}
                      {c.potPct.toFixed(1)}%
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Cumulative P/L Chart + Factor Attribution */}
      <div className="grid gap-4 lg:grid-cols-12">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 lg:col-span-7">
          <h3 className="mb-3 text-sm font-bold text-white">Cumulative Net P/L Curve ($10 Units)</h3>
          {stats.cumulative.length === 0 ? (
            <p className="py-12 text-center text-xs text-slate-500">No settled bets match the current filters.</p>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={stats.cumulative}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="label" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                  <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={(v) => `$${v}`} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#0f172a',
                      border: '1px solid #334155',
                      borderRadius: '10px',
                      fontSize: '12px',
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: '11px' }} />
                  <Line type="monotone" dataKey="winOverlayCum" name="Win (Overlays)" stroke="#10b981" strokeWidth={2.5} dot={{ r: 3 }} />
                  <Line type="monotone" dataKey="winAllCum" name="Win (All)" stroke="#38bdf8" strokeWidth={2} dot={{ r: 2 }} />
                  <Line type="monotone" dataKey="srm2Cum" name="2-Leg SRM" stroke="#a78bfa" strokeWidth={2} dot={{ r: 2 }} />
                  <Line type="monotone" dataKey="exoticsCum" name="Exotics" stroke="#f59e0b" strokeWidth={2} dot={{ r: 2 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {/* 23-Factor Weight Tuning Diagnostics */}
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 lg:col-span-5">
          <h3 className="text-sm font-bold text-white">Factor Attribution (Weight Tuning)</h3>
          <p className="mb-2.5 text-[11px] text-slate-400">
            Compares average factor score on winning vs. losing #1 selections to guide edits in <code>factor_weights.json</code>.
          </p>
          <div className="max-h-60 overflow-y-auto pr-1">
            <table className="w-full text-left text-[11px]">
              <thead className="sticky top-0 bg-slate-900 text-slate-400">
                <tr>
                  <th className="py-1.5">Factor</th>
                  <th className="py-1.5 text-right">Win vs Loss</th>
                  <th className="py-1.5 text-right">Signal</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 tabular-nums">
                {stats.factorDiagnostics
                  .filter((f) => f.sampleCount > 0)
                  .map((f) => (
                    <tr key={f.key}>
                      <td className="py-1.5 font-medium text-slate-200">{f.label}</td>
                      <td className="py-1.5 text-right text-slate-300">
                        {f.avgWinScore.toFixed(1)} vs {f.avgLossScore.toFixed(1)}{' '}
                        <span className={f.edgeDelta >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                          ({f.edgeDelta >= 0 ? '+' : ''}
                          {f.edgeDelta.toFixed(1)})
                        </span>
                      </td>
                      <td className="py-1.5 text-right">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                            f.recommendation === 'Boost Weight'
                              ? 'bg-emerald-500/20 text-emerald-300'
                              : f.recommendation === 'Trim Weight'
                              ? 'bg-rose-500/20 text-rose-300'
                              : 'bg-slate-800 text-slate-400'
                          }`}
                        >
                          {f.recommendation}
                        </span>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Settled Ledger with Manual Actual-Odds Overrides */}
      <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/70">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-4 py-3">
          <div>
            <h3 className="flex items-center gap-1.5 text-sm font-bold text-white">
              <Edit3 className="h-4 w-4 text-emerald-400" /> Settled Bets Ledger &amp; Manual Actual-Odds Overrides
            </h3>
            <p className="text-xs text-slate-400">
              Override any settled Win, SRM (0.75x est.), or Exotic dividend with your actual bookmaker payout odds; saved locally and reflected instantly above.
            </p>
          </div>
          {Object.keys(overrides).length > 0 && (
            <button
              type="button"
              onClick={resetAllOverrides}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-700"
            >
              <RotateCcw className="h-3.5 w-3.5" /> Reset Overrides ({Object.keys(overrides).length})
            </button>
          )}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-800 bg-slate-950/60 text-[11px] uppercase tracking-wider text-slate-400">
              <tr>
                <th className="px-3 py-2.5">Date / Race</th>
                <th className="px-3 py-2.5">Conditions</th>
                <th className="px-3 py-2.5">#1 Selection</th>
                <th className="px-3 py-2.5 text-right">SP / BSP</th>
                <th className="px-3 py-2.5 text-right">Win Override</th>
                <th className="px-3 py-2.5 text-right">2-Leg SRM Override</th>
                <th className="px-3 py-2.5 text-right">3-Leg SRM Override</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70 tabular-nums">
              {filtered.map((s) => {
                const srm2 = s.srm?.find((x) => x.leg_count === 2);
                const srm3 = s.srm?.find((x) => x.leg_count === 3);
                return (
                  <tr key={s.settlement_id} className="hover:bg-slate-800/40">
                    <td className="px-3 py-2.5">
                      <div className="font-semibold text-slate-100">
                        {s.meeting_name} {s.kind === 'quaddie' ? 'Quaddie' : `R${s.race_number}`}
                      </div>
                      <div className="text-[11px] text-slate-400">
                        {s.date} · {s.tier}
                      </div>
                    </td>
                    <td className="px-3 py-2.5 text-[11px] text-slate-300">
                      {s.track_condition} · {s.distance_band} · {s.fluc_direction}
                    </td>
                    <td className="px-3 py-2.5">
                      {s.win_all ? (
                        <div>
                          <span className="font-semibold text-slate-100">
                            #{s.win_all.runner_number} {s.win_all.runner_name}
                          </span>{' '}
                          <span
                            className={`ml-1 rounded px-1.5 py-0.5 text-[10px] font-bold ${
                              s.win_all.won ? 'bg-emerald-500/20 text-emerald-300' : 'bg-slate-800 text-slate-400'
                            }`}
                          >
                            {s.win_all.won ? 'WON' : `${s.win_all.finish_position || '—'}th`}
                          </span>
                        </div>
                      ) : (
                        <span className="text-slate-500">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right text-slate-300">
                      {s.win_all ? `$${s.win_all.settled_odds.toFixed(2)}` : '—'}
                      {s.win_all?.betfair_bsp ? (
                        <div className="text-[10px] text-amber-300">BSP ${s.win_all.betfair_bsp.toFixed(2)}</div>
                      ) : null}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      {s.win_all ? (
                        <input
                          type="number"
                          step="0.05"
                          placeholder={s.win_all.settled_odds.toFixed(2)}
                          value={overrides[overrideKey(s.settlement_id, 'win_all')] ?? ''}
                          onChange={(e) => updateOverride(s.settlement_id, 'win_all', e.target.value)}
                          className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-right text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
                        />
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      {srm2 ? (
                        <div className="flex items-center justify-end gap-1.5">
                          <span className={`text-[10px] font-bold ${srm2.won ? 'text-emerald-400' : 'text-slate-500'}`}>
                            {srm2.won ? 'W' : 'L'}
                          </span>
                          <input
                            type="number"
                            step="0.05"
                            placeholder={srm2.settled_odds.toFixed(2)}
                            value={overrides[overrideKey(s.settlement_id, 'srm_2leg')] ?? ''}
                            onChange={(e) => updateOverride(s.settlement_id, 'srm_2leg', e.target.value)}
                            className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-right text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
                          />
                        </div>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      {srm3 ? (
                        <div className="flex items-center justify-end gap-1.5">
                          <span className={`text-[10px] font-bold ${srm3.won ? 'text-emerald-400' : 'text-slate-500'}`}>
                            {srm3.won ? 'W' : 'L'}
                          </span>
                          <input
                            type="number"
                            step="0.05"
                            placeholder={srm3.settled_odds.toFixed(2)}
                            value={overrides[overrideKey(s.settlement_id, 'srm_3leg')] ?? ''}
                            onChange={(e) => updateOverride(s.settlement_id, 'srm_3leg', e.target.value)}
                            className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-right text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
                          />
                        </div>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
