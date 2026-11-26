import React, { useMemo, useState } from 'react';
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
import {
  SlidersHorizontal,
  TrendingUp,
  DollarSign,
  Target,
  Edit3,
  RotateCcw,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Trophy,
  CheckCircle2,
  ShieldCheck,
} from 'lucide-react';
import type { MeetingDoc, RaceDoc, SettlementDoc } from '../lib/types';
import {
  COMBINED_CATEGORY_OPTIONS,
  MIN_SAMPLE_WINS_FOR_TUNING,
  computeCoverageAudit,
  computeProfitability,
  filterSettlements,
  formatOrdinal,
  getLedgerBetDetails,
  loadOddsOverrides,
  saveOddsOverrides,
  type OddsOverrideMap,
  type ProfitabilityFilters,
} from '../lib/profitability';

interface Props {
  settlements: SettlementDoc[];
  auditMeetings?: MeetingDoc[];
  auditRaces?: RaceDoc[];
  selectedDate?: string;
  loading?: boolean;
}

const LEDGER_BET_FILTERS: { key: string; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'win_all', label: 'Win' },
  { key: 'win_overlay', label: 'Win Overlay' },
  { key: 'srm_2leg', label: '2-Leg SRM' },
  { key: 'srm_3leg', label: '3-Leg SRM' },
  { key: 'srm_4leg', label: '4-Leg SRM' },
  { key: 'exacta', label: 'Exacta' },
  { key: 'trifecta', label: 'Trifecta' },
  { key: 'first4', label: 'First 4' },
  { key: 'quaddie', label: 'Quaddie' },
];

export function TabProfitability({
  settlements,
  auditMeetings = [],
  auditRaces = [],
  selectedDate,
  loading = false,
}: Props) {
  const [filters, setFilters] = useState<ProfitabilityFilters>({
    trackCondition: 'All',
    distanceBand: 'All',
    flucDirection: 'All',
    tier: 'All',
  });
  const [overrides, setOverrides] = useState<OddsOverrideMap>(() => loadOddsOverrides());
  const [selectedCombinedKeys, setSelectedCombinedKeys] = useState<string[]>(['win_all']);
  const [ledgerBetFilter, setLedgerBetFilter] = useState<string>('all');
  const [wonOnly, setWonOnly] = useState<boolean>(false);
  const [expandedRowId, setExpandedRowId] = useState<string | null>(null);
  const [auditScope, setAuditScope] = useState<'overall' | 'selected'>('overall');

  const overallAudit = useMemo(
    () => computeCoverageAudit(auditMeetings, auditRaces, settlements),
    [auditMeetings, auditRaces, settlements]
  );
  const dateAudit = useMemo(
    () => computeCoverageAudit(auditMeetings, auditRaces, settlements, selectedDate),
    [auditMeetings, auditRaces, settlements, selectedDate]
  );
  const activeAudit = auditScope === 'selected' && selectedDate ? dateAudit : overallAudit;
  const totalMissing = activeAudit.missingRaces.length + activeAudit.missingQuaddies.length;

  const toggleCombinedKey = (key: string) => {
    setSelectedCombinedKeys((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  };

  const filtered = useMemo(() => filterSettlements(settlements, filters), [settlements, filters]);
  const stats = useMemo(
    () => computeProfitability(filtered, overrides, selectedCombinedKeys),
    [filtered, overrides, selectedCombinedKeys]
  );

  const ledgerRows = useMemo(() => {
    return filtered
      .map((s) => {
        const allBets = getLedgerBetDetails(s, overrides);
        const matchingBets =
          ledgerBetFilter === 'all' ? allBets : allBets.filter((b) => b.betKey === ledgerBetFilter);
        return { settlement: s, allBets, matchingBets };
      })
      .filter(({ matchingBets }) => {
        if (matchingBets.length === 0) return false;
        if (wonOnly) return matchingBets.some((b) => b.status === 'W');
        return true;
      });
  }, [filtered, overrides, ledgerBetFilter, wonOnly]);

  const updateOverrideByKey = (oKey: string, val: string) => {
    const next = { ...overrides };
    const num = Number(val);
    if (!val.trim() || Number.isNaN(num) || num <= 0) {
      delete next[oKey];
    } else {
      next[oKey] = num;
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
  const liveFactorCount = stats.factorDiagnostics.filter((f) => f.hasLiveData).length;
  const neutralFactorCount = stats.factorDiagnostics.length - liveFactorCount;

  if (!loading && settlements.length === 0) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-10 text-center">
        <h2 className="text-base font-bold text-white">No Settled Races in Firestore Yet</h2>
        <p className="mx-auto mt-2 max-w-md text-xs text-slate-400">
          Only races with locked 24-Point selections are settled here. Once races finish,{' '}
          <code>post-race-settle.yml</code> automatically records official tote dividends and Betfair BSPs every 45 minutes.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Coverage & Integrity Audit Strip */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-800 bg-slate-900/80 px-4 py-3">
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <div className="flex items-center gap-1.5 font-bold text-slate-100">
            <ShieldCheck className="h-4 w-4 text-emerald-400" />
            <span>Coverage &amp; Integrity Audit</span>
          </div>

          <div className="inline-flex rounded-lg border border-slate-800 bg-slate-950 p-0.5 text-[11px]">
            <button
              type="button"
              onClick={() => setAuditScope('overall')}
              className={`rounded-md px-2.5 py-0.5 font-semibold transition ${
                auditScope === 'overall' ? 'bg-slate-800 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Overall
            </button>
            {selectedDate && (
              <button
                type="button"
                onClick={() => setAuditScope('selected')}
                className={`rounded-md px-2.5 py-0.5 font-semibold transition ${
                  auditScope === 'selected' ? 'bg-slate-800 text-white' : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {selectedDate}
              </button>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 tabular-nums">
            <span className="rounded-lg border border-slate-800 bg-slate-950/70 px-2.5 py-1 text-slate-300">
              Tracked Races Settled:{' '}
              <strong className="text-white">
                {activeAudit.trackedSettled} / {activeAudit.trackedFinished}
              </strong>
            </span>
            <span className="rounded-lg border border-slate-800 bg-slate-950/70 px-2.5 py-1 text-slate-300">
              Quaddies Settled:{' '}
              <strong className="text-white">
                {activeAudit.quaddiesSettled} / {activeAudit.quaddiesEligible}
              </strong>
            </span>
            <span
              className="rounded-lg border border-slate-800 bg-slate-950/70 px-2.5 py-1 text-slate-400"
              title="Historical FINAL · NO PICK races finished without a TurfEdge selection and excluded from POT%."
            >
              Untracked (No Pick): <strong className="text-slate-200">{activeAudit.untrackedNoPick}</strong>
            </span>
            {activeAudit.upcomingTracked > 0 && (
              <span className="rounded-lg border border-sky-500/25 bg-sky-500/10 px-2.5 py-1 text-sky-300">
                Upcoming Tracked: <strong>{activeAudit.upcomingTracked}</strong>
              </span>
            )}
          </div>
        </div>

        {totalMissing === 0 ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/40 bg-emerald-500/15 px-3 py-1 text-xs font-bold text-emerald-300">
            <CheckCircle2 className="h-3.5 w-3.5" /> 0 Missing
          </span>
        ) : (
          <span
            title={`Awaiting settlement: ${[...activeAudit.missingRaces, ...activeAudit.missingQuaddies].join(', ')}`}
            className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/45 bg-amber-500/15 px-3 py-1 text-xs font-bold text-amber-300"
          >
            <AlertTriangle className="h-3.5 w-3.5" /> {totalMissing} Awaiting Settlement
          </span>
        )}
      </div>

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

      {/* Combined KPI Rollup Selector Checkboxes */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            Combined KPI Rollup Categories (defaults to Win All)
          </span>
          <div className="flex flex-wrap items-center gap-2">
            {COMBINED_CATEGORY_OPTIONS.map((opt) => {
              const checked = selectedCombinedKeys.includes(opt.key);
              return (
                <label
                  key={opt.key}
                  className={`inline-flex cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs transition ${
                    checked
                      ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-200'
                      : 'border-slate-800 bg-slate-950/70 text-slate-400 hover:border-slate-700 hover:text-slate-200'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleCombinedKey(opt.key)}
                    className="h-3.5 w-3.5 rounded border-slate-600 bg-slate-900 accent-emerald-500"
                  />
                  <span>{opt.shortLabel}</span>
                </label>
              );
            })}
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
            Staked ${stats.overall.totalStaked.toFixed(0)} · Return ${stats.overall.totalReturn.toFixed(2)} ({selectedCombinedKeys.length} selected)
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

      {/* Split Cumulative Net P/L Curves: Chart 1 (Win & SRMs) + Chart 2 (Exotics) */}
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <h3 className="mb-3 text-sm font-bold text-white">Win &amp; Same Race Multi Cumulative P/L ($10 Units)</h3>
          {stats.cumulative.length === 0 ? (
            <p className="py-12 text-center text-xs text-slate-500">No settled bets match the current filters.</p>
          ) : (
            <div className="h-72 w-full">
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
                  <Line type="monotone" dataKey="winAllCum" name="Win (All)" stroke="#38bdf8" strokeWidth={2} dot={{ r: 2 }} />
                  <Line type="monotone" dataKey="winOverlayCum" name="Win (Overlays)" stroke="#10b981" strokeWidth={2.5} dot={{ r: 2.5 }} />
                  <Line type="monotone" dataKey="srm2Cum" name="2-Leg SRM" stroke="#a78bfa" strokeWidth={2} dot={{ r: 2 }} />
                  <Line type="monotone" dataKey="srm3Cum" name="3-Leg SRM" stroke="#f472b6" strokeWidth={2} dot={{ r: 2 }} />
                  <Line type="monotone" dataKey="srm4Cum" name="4-Leg SRM" stroke="#fb923c" strokeWidth={2} dot={{ r: 2 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <h3 className="mb-3 text-sm font-bold text-white">Exotics Cumulative P/L ($10 Flexi Units)</h3>
          {stats.cumulative.length === 0 ? (
            <p className="py-12 text-center text-xs text-slate-500">No settled bets match the current filters.</p>
          ) : (
            <div className="h-72 w-full">
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
                  <Line type="monotone" dataKey="exactaCum" name="Exacta" stroke="#f59e0b" strokeWidth={2} dot={{ r: 2 }} />
                  <Line type="monotone" dataKey="trifectaCum" name="Trifecta" stroke="#22d3ee" strokeWidth={2} dot={{ r: 2 }} />
                  <Line type="monotone" dataKey="first4Cum" name="First 4" stroke="#34d399" strokeWidth={2.5} dot={{ r: 2.5 }} />
                  <Line type="monotone" dataKey="quaddieCum" name="Quaddie" stroke="#c084fc" strokeWidth={2} dot={{ r: 2 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* All-24 Factor Attribution (Never silently omits neutral factors) */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-bold text-white">24-Factor Attribution (Weight Tuning)</h3>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-bold text-emerald-300">
                {liveFactorCount} LIVE
              </span>
              <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-bold text-slate-400">
                {neutralFactorCount} NEUTRAL 5.0
              </span>
              {stats.totalWinCount < MIN_SAMPLE_WINS_FOR_TUNING && (
                <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-500/15 px-2 py-0.5 text-[10px] font-bold text-amber-300">
                  <AlertTriangle className="h-3 w-3" /> {stats.totalWinCount}/{MIN_SAMPLE_WINS_FOR_TUNING} Wins
                </span>
              )}
            </div>
          </div>
          <p className="mb-2.5 mt-1 text-[11px] text-slate-400">
            All 24 factors shown. Signals stay <strong>Low Sample</strong> until 100+ wins; then Δ ≥ +0.5 → Boost, ≤ −0.5 → Trim.
          </p>
          <div className="max-h-72 overflow-y-auto pr-1">
            <table className="w-full text-left text-[11px]">
              <thead className="sticky top-0 bg-slate-900 text-slate-400">
                <tr>
                  <th className="py-1.5">Factor (24)</th>
                  <th className="py-1.5">Source</th>
                  <th className="py-1.5 text-right">Win vs Loss</th>
                  <th className="py-1.5 text-right">Signal</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 tabular-nums">
                {stats.factorDiagnostics.map((f) => (
                  <tr key={f.key} className={!f.hasLiveData ? 'opacity-65' : ''}>
                    <td className="py-1.5 pr-2 font-medium text-slate-200">{f.label}</td>
                    <td className="py-1.5 pr-2">
                      {f.hasLiveData ? (
                        <span className="rounded bg-emerald-500/15 px-1.5 py-0.2 text-[9px] font-bold uppercase text-emerald-300 ring-1 ring-emerald-500/30">
                          LIVE
                        </span>
                      ) : (
                        <span className="rounded bg-slate-800 px-1.5 py-0.2 text-[9px] font-bold uppercase text-slate-400 ring-1 ring-slate-700">
                          NEUTRAL 5.0
                        </span>
                      )}
                    </td>
                    <td className="py-1.5 pr-2 text-right text-slate-300">
                      {f.avgWinScore.toFixed(1)} vs {f.avgLossScore.toFixed(1)}{' '}
                      <span
                        className={
                          !f.hasLiveData
                            ? 'text-slate-500'
                            : f.edgeDelta >= 0
                            ? 'text-emerald-400'
                            : 'text-rose-400'
                        }
                      >
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

      {/* Settled Bets Ledger — All 9 Bet Types, Filters & Expandable Overrides */}
      <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/70">
        <div className="border-b border-slate-800 px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="flex items-center gap-1.5 text-sm font-bold text-white">
                <Edit3 className="h-4 w-4 text-emerald-400" /> Settled Bets Ledger &amp; Manual Actual-Dividend Overrides
              </h3>
              <p className="text-xs text-slate-400">
                Click any row to inspect full combos/returns and override actual bookmaker odds or exotic dividends for all 9 bet types.
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

          {/* Bet Type Filter Pills + Won Only Toggle */}
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              {LEDGER_BET_FILTERS.map((bf) => (
                <button
                  key={bf.key}
                  type="button"
                  onClick={() => setLedgerBetFilter(bf.key)}
                  className={`rounded-lg px-2.5 py-1 text-[11px] font-semibold transition ${
                    ledgerBetFilter === bf.key
                      ? 'bg-emerald-500 text-slate-950'
                      : 'border border-slate-800 bg-slate-950/70 text-slate-400 hover:border-slate-700 hover:text-slate-200'
                  }`}
                >
                  {bf.label}
                </button>
              ))}
            </div>

            <button
              type="button"
              onClick={() => setWonOnly((v) => !v)}
              className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1 text-[11px] font-bold transition ${
                wonOnly
                  ? 'border-emerald-500 bg-emerald-500/20 text-emerald-300'
                  : 'border-slate-700 bg-slate-950 text-slate-400 hover:text-slate-200'
              }`}
            >
              <Trophy className="h-3.5 w-3.5" /> Won Only {wonOnly ? 'ON' : 'OFF'}
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-800 bg-slate-950/60 text-[11px] uppercase tracking-wider text-slate-400">
              <tr>
                <th className="px-3 py-2.5">Date / Race</th>
                <th className="px-3 py-2.5">Conditions</th>
                <th className="px-3 py-2.5">#1 Selection / Quaddie</th>
                <th className="px-3 py-2.5">All Bet Outcomes (Click Row for Combos &amp; Overrides)</th>
                <th className="px-3 py-2.5 text-right">Row Net</th>
                <th className="px-3 py-2.5"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70 tabular-nums">
              {ledgerRows.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-xs text-slate-500">
                    No settled bets match this bet type / Won Only filter.
                  </td>
                </tr>
              ) : (
                ledgerRows.map(({ settlement: s, allBets, matchingBets }) => {
                  const isOpen = expandedRowId === s.settlement_id;
                  // Row Net sums matching non-overlapping bets (exclude win_overlay if win_all is also in matchingBets)
                  const hasWinAll = matchingBets.some((b) => b.betKey === 'win_all');
                  const rowNet = matchingBets
                    .filter((b) => !(hasWinAll && b.betKey === 'win_overlay'))
                    .reduce((acc, b) => acc + b.profit, 0);

                  return (
                    <React.Fragment key={s.settlement_id}>
                      <tr
                        onClick={() => setExpandedRowId(isOpen ? null : s.settlement_id)}
                        className={`cursor-pointer transition hover:bg-slate-800/45 ${
                          isOpen ? 'bg-slate-800/35' : ''
                        }`}
                      >
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
                          {s.kind === 'quaddie' && s.quaddie ? (
                            <div>
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className="font-mono text-xs font-bold text-emerald-300">
                                  {s.quaddie.display}
                                </span>
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                                    s.quaddie.won
                                      ? 'bg-emerald-500/20 text-emerald-300'
                                      : 'bg-rose-500/15 text-rose-300'
                                  }`}
                                >
                                  {s.quaddie.won ? 'W' : 'L'}
                                </span>
                                {s.quaddie.legs_won !== undefined && (
                                  <span className="text-[10px] text-slate-400">
                                    ({s.quaddie.legs_won}/4 legs)
                                  </span>
                                )}
                              </div>
                            </div>
                          ) : s.win_all ? (
                            <div>
                              <span className="font-semibold text-slate-100">
                                #{s.win_all.runner_number} {s.win_all.runner_name}
                              </span>{' '}
                              <span
                                className={`ml-1 rounded px-1.5 py-0.5 text-[10px] font-bold ${
                                  s.win_all.void || s.win_all.scratched
                                    ? 'bg-amber-500/20 text-amber-300'
                                    : s.win_all.won
                                    ? 'bg-emerald-500/20 text-emerald-300'
                                    : 'bg-slate-800 text-slate-400'
                                }`}
                              >
                                {s.win_all.won
                                  ? '1st · WON'
                                  : formatOrdinal(s.win_all.finish_position, s.win_all.void, s.win_all.scratched)}
                              </span>
                            </div>
                          ) : (
                            <span className="text-slate-500">—</span>
                          )}
                        </td>
                        <td className="px-3 py-2.5">
                          <div className="flex flex-wrap gap-1.5">
                            {matchingBets.map((b) => {
                              const isUnderStakeWin = b.status === 'W' && b.profit < 0;
                              const pillStyle = isUnderStakeWin
                                ? 'border-amber-500/40 bg-amber-500/15 text-amber-300'
                                : b.status === 'W'
                                ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-200'
                                : b.status === 'VOID'
                                ? 'border-amber-500/35 bg-amber-500/10 text-amber-300'
                                : 'border-slate-800 bg-slate-950/70 text-slate-400';
                              return (
                                <span
                                  key={b.betKey}
                                  className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-semibold ${pillStyle}`}
                                  title={
                                    b.betKey === 'win_overlay'
                                      ? `Subset of Win bets meeting the Live > Rated value overlay threshold (not double-staked $10 in Row Net). ${b.comboText}`
                                      : `${b.label}: ${b.comboText}${
                                          isUnderStakeWin
                                            ? ` (Under-stake hit: returned $${b.effectiveReturn.toFixed(2)} on $${b.stake.toFixed(0)})`
                                            : ''
                                        }`
                                  }
                                >
                                  <span>{b.shortTag}</span>
                                  <strong
                                    className={
                                      isUnderStakeWin
                                        ? 'text-amber-300'
                                        : b.status === 'W'
                                        ? 'text-emerald-300'
                                        : b.status === 'VOID'
                                        ? 'text-amber-300'
                                        : 'text-slate-500'
                                    }
                                  >
                                    {b.status}
                                  </strong>
                                  {b.status === 'W' && b.effectiveReturn > 0 && (
                                    <span className={isUnderStakeWin ? 'text-amber-300' : 'text-emerald-300'}>
                                      ${b.effectiveReturn.toFixed(isUnderStakeWin ? 2 : 0)}
                                    </span>
                                  )}
                                  {b.status !== 'W' && b.officialOdds && b.status !== 'VOID' && (
                                    <span className="text-slate-500">
                                      ${b.officialOdds.toFixed(1)}
                                    </span>
                                  )}
                                  {b.hasOverride && (
                                    <span className="h-1.5 w-1.5 rounded-full bg-sky-400" title="Manual override applied" />
                                  )}
                                </span>
                              );
                            })}
                          </div>
                        </td>
                        <td
                          className={`px-3 py-2.5 text-right font-bold ${
                            rowNet > 0 ? 'text-emerald-400' : rowNet < 0 ? 'text-rose-400' : 'text-slate-400'
                          }`}
                        >
                          {rowNet >= 0 ? '+' : ''}${rowNet.toFixed(2)}
                        </td>
                        <td className="px-3 py-2.5 text-slate-400">
                          {isOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                        </td>
                      </tr>

                      {isOpen && (
                        <tr>
                          <td colSpan={6} className="bg-slate-950/90 p-4">
                            <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                              <span className="font-bold text-slate-100">
                                Full Bet Breakdown &amp; Actual-Dividend Overrides — {s.meeting_name}{' '}
                                {s.kind === 'quaddie' ? 'Quaddie' : `R${s.race_number}`}
                              </span>
                              {s.top4_order && s.top4_order.length > 0 && (
                                <span className="text-[11px] text-slate-400">
                                  Official Top 4: <strong className="text-slate-200">{s.top4_order.join(' - ')}</strong>
                                </span>
                              )}
                            </div>

                            <div className="grid gap-2.5 md:grid-cols-2 xl:grid-cols-3">
                              {allBets.map((b) => (
                                <div
                                  key={b.betKey}
                                  className="rounded-xl border border-slate-800 bg-slate-900/75 p-3 text-xs"
                                >
                                  <div className="flex items-center justify-between gap-2">
                                    <span className="font-bold text-slate-100">{b.label}</span>
                                    <span
                                      className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                                        b.status === 'W' && b.profit < 0
                                          ? 'bg-amber-500/20 text-amber-300'
                                          : b.status === 'W'
                                          ? 'bg-emerald-500/20 text-emerald-300'
                                          : b.status === 'VOID'
                                          ? 'bg-amber-500/20 text-amber-300'
                                          : 'bg-slate-800 text-slate-400'
                                      }`}
                                    >
                                      {b.status === 'W' && b.profit < 0
                                        ? 'WON (UNDER STAKE)'
                                        : b.status === 'W'
                                        ? 'WON'
                                        : b.status === 'VOID'
                                        ? 'VOID ($0)'
                                        : 'LOST'}
                                    </span>
                                  </div>
                                  <div className="mt-1 font-mono text-[11px] text-slate-200">{b.comboText}</div>
                                  {b.subText && <div className="mt-0.5 text-[10px] text-slate-400">{b.subText}</div>}

                                  <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-slate-800 pt-2">
                                    <div className="text-[11px] text-slate-300">
                                      Stake ${b.stake.toFixed(0)} → Return{' '}
                                      <strong className="text-white">${b.effectiveReturn.toFixed(2)}</strong>{' '}
                                      <span className={b.profit >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                                        ({b.profit >= 0 ? '+' : ''}${b.profit.toFixed(2)})
                                      </span>
                                    </div>
                                    {b.status !== 'VOID' && b.betKey !== 'win_overlay' && (
                                      <div className="flex items-center gap-1">
                                        <span className="text-[10px] text-slate-500">
                                          {b.isFlexi ? 'Div $' : 'Odds $'}
                                        </span>
                                        <input
                                          type="number"
                                          step={b.isFlexi ? '0.5' : '0.05'}
                                          placeholder={b.officialOdds ? b.officialOdds.toFixed(2) : 'Override'}
                                          value={overrides[b.overrideKey] ?? ''}
                                          onClick={(e) => e.stopPropagation()}
                                          onChange={(e) => updateOverrideByKey(b.overrideKey, e.target.value)}
                                          className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-0.5 text-right text-[11px] text-slate-100 focus:border-emerald-500 focus:outline-none"
                                        />
                                      </div>
                                    )}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
