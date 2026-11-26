import React, { useEffect, useMemo, useState } from 'react';
import {
  ChevronDown,
  ChevronUp,
  Flame,
  TrendingUp,
  TrendingDown,
  Minus,
  Trophy,
  Layers,
  Upload,
  MapPin,
  Gauge,
} from 'lucide-react';
import type { MeetingDoc, RaceDoc } from '../lib/types';
import { WindCompass } from './WindCompass';
import { RunnerAuditDrawer } from './RunnerAuditDrawer';
import { ManualDropzone } from './ManualDropzone';

interface Props {
  dates: string[];
  selectedDate: string;
  onDateChange: (d: string) => void;
  meetings: MeetingDoc[];
  races: RaceDoc[];
  onManualRaceAdded: (race: RaceDoc) => void;
}

function FlucBadge({ direction, pct }: { direction: string; pct: number }) {
  if (direction === 'Firmer') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-300 ring-1 ring-emerald-500/30 tabular-nums">
        <TrendingUp className="h-3 w-3" /> Firmer {pct >= 0 ? `+${pct.toFixed(1)}%` : `${pct.toFixed(1)}%`}
      </span>
    );
  }
  if (direction === 'Drifter') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/15 px-2 py-0.5 text-[11px] font-semibold text-rose-300 ring-1 ring-rose-500/30 tabular-nums">
        <TrendingDown className="h-3 w-3" /> Drifter {pct.toFixed(1)}%
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-slate-800 px-2 py-0.5 text-[11px] font-medium text-slate-300 tabular-nums">
      <Minus className="h-3 w-3" /> Steady {pct >= 0 ? `+${pct.toFixed(1)}%` : `${pct.toFixed(1)}%`}
    </span>
  );
}

export function TabUpcoming({
  dates,
  selectedDate,
  onDateChange,
  meetings,
  races,
  onManualRaceAdded,
}: Props) {
  const [tierFilter, setTierFilter] = useState<'All' | 'Metro' | 'Provincial'>('All');
  const [selectedMeetingSlug, setSelectedMeetingSlug] = useState<string>('');
  const [selectedRaceId, setSelectedRaceId] = useState<string>('');
  const [expandedRunner, setExpandedRunner] = useState<number | null>(null);
  const [showDropzone, setShowDropzone] = useState(false);

  // Reset filters/selection cleanly whenever the user switches dates so all historical meetings show
  useEffect(() => {
    setTierFilter('All');
    setSelectedMeetingSlug('');
    setSelectedRaceId('');
    setExpandedRunner(null);
  }, [selectedDate]);

  // Build unified list of ALL meetings for the selected date regardless of race status (Settled/Closed/Open)
  const allMeetingsForDate = useMemo(() => {
    const map = new Map<string, MeetingDoc>();
    meetings.forEach((m) => {
      if (m.meeting_slug) map.set(m.meeting_slug, m);
    });
    races.forEach((r) => {
      if (r.meeting_slug && !map.has(r.meeting_slug)) {
        map.set(r.meeting_slug, {
          meeting_slug: r.meeting_slug,
          date: r.date,
          name: r.meeting_name,
          display_name: r.meeting_name,
          state: r.state,
          tier: r.tier,
          track_condition: r.track_condition,
          track_condition_band: r.track_condition_band,
          rail_position: r.rail_position,
          quaddie_races: [],
          weather: r.weather,
          meeting_bias: r.meeting_bias,
          race_count: races.filter((x) => x.meeting_slug === r.meeting_slug).length,
        });
      }
    });
    return Array.from(map.values()).sort((a, b) =>
      (a.display_name || a.name || '').localeCompare(b.display_name || b.name || '')
    );
  }, [meetings, races]);

  const tierCounts = useMemo(
    () => ({
      All: allMeetingsForDate.length,
      Metro: allMeetingsForDate.filter((m) => m.tier === 'Metro').length,
      Provincial: allMeetingsForDate.filter((m) => m.tier === 'Provincial').length,
    }),
    [allMeetingsForDate]
  );

  const filteredMeetings = useMemo(() => {
    return allMeetingsForDate.filter((m) => tierFilter === 'All' || m.tier === tierFilter);
  }, [allMeetingsForDate, tierFilter]);

  const activeMeeting = useMemo(() => {
    return (
      filteredMeetings.find((m) => m.meeting_slug === selectedMeetingSlug) ||
      filteredMeetings[0] ||
      null
    );
  }, [filteredMeetings, selectedMeetingSlug]);

  const meetingRaces = useMemo(() => {
    if (!activeMeeting) return [];
    return races
      .filter((r) => r.meeting_slug === activeMeeting.meeting_slug)
      .sort((a, b) => a.race_number - b.race_number);
  }, [races, activeMeeting]);

  const activeRace = useMemo(() => {
    return meetingRaces.find((r) => r.race_id === selectedRaceId) || meetingRaces[0] || null;
  }, [meetingRaces, selectedRaceId]);

  const winSel = activeRace?.win_selection || null;

  return (
    <div className="space-y-5">
      {/* Filter Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-800 bg-slate-900/70 p-3.5">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex items-center gap-1.5">
            <label className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Date</label>
            <select
              value={selectedDate}
              onChange={(e) => onDateChange(e.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs font-medium text-slate-100 focus:border-emerald-500 focus:outline-none"
            >
              {dates.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </div>

          <div className="inline-flex rounded-lg border border-slate-800 bg-slate-950 p-0.5 text-xs">
            {(['All', 'Metro', 'Provincial'] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTierFilter(t)}
                className={`rounded-md px-3 py-1 font-medium transition ${
                  tierFilter === t ? 'bg-emerald-500 text-slate-950' : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {t} <span className="opacity-75">({tierCounts[t]})</span>
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1.5">
            <label className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Meeting</label>
            <select
              value={activeMeeting?.meeting_slug || ''}
              onChange={(e) => {
                setSelectedMeetingSlug(e.target.value);
                setSelectedRaceId('');
                setExpandedRunner(null);
              }}
              className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs font-medium text-slate-100 focus:border-emerald-500 focus:outline-none"
            >
              {filteredMeetings.map((m) => (
                <option key={m.meeting_slug} value={m.meeting_slug}>
                  {m.display_name} ({m.state} · {m.tier})
                </option>
              ))}
            </select>
          </div>
        </div>

        <button
          type="button"
          onClick={() => setShowDropzone((s) => !s)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-1.5 text-xs font-semibold text-slate-200 hover:bg-slate-700"
        >
          <Upload className="h-3.5 w-3.5 text-emerald-400" />
          {showDropzone ? 'Hide Manual CSV/JSON' : 'Manual CSV/JSON Fallback'}
        </button>
      </div>

      {/* Clickable Meeting Pills for All Meetings on Selected Date */}
      {filteredMeetings.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          {filteredMeetings.map((m) => {
            const isSelected = activeMeeting?.meeting_slug === m.meeting_slug;
            const rCount = races.filter((r) => r.meeting_slug === m.meeting_slug).length || m.race_count || 0;
            return (
              <button
                key={m.meeting_slug}
                type="button"
                onClick={() => {
                  setSelectedMeetingSlug(m.meeting_slug);
                  setSelectedRaceId('');
                  setExpandedRunner(null);
                }}
                className={`inline-flex items-center gap-2 rounded-xl border px-3 py-1.5 text-xs font-semibold transition ${
                  isSelected
                    ? 'border-emerald-500 bg-emerald-500/15 text-emerald-200'
                    : 'border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700 hover:text-white'
                }`}
              >
                <span>{m.display_name}</span>
                <span className="text-[10px] font-normal text-slate-400">
                  {m.state} · {m.tier} · {rCount}R
                </span>
              </button>
            );
          })}
        </div>
      )}

      {showDropzone && (
        <ManualDropzone
          onRaceScored={(newRace) => {
            onManualRaceAdded(newRace);
            setSelectedMeetingSlug(newRace.meeting_slug);
            setSelectedRaceId(newRace.race_id);
            setShowDropzone(false);
          }}
        />
      )}

      {/* Race Selector Strip */}
      {meetingRaces.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {meetingRaces.map((r) => {
            const isSelected = activeRace?.race_id === r.race_id;
            const hasOverlay = r.win_selection?.is_value_overlay;
            const isFinished = ['final', 'settled', 'closed', 'paying'].includes((r.status || '').toLowerCase());
            const isTracked = Boolean(r.pre_jump_analyzed || r.baseline_analyzed);
            const isFinalNoPick = !r.settled && isFinished && !isTracked;
            return (
              <button
                key={r.race_id}
                type="button"
                onClick={() => {
                  setSelectedRaceId(r.race_id);
                  setExpandedRunner(null);
                }}
                title={
                  isFinalNoPick
                    ? 'This race finished without a pre-jump TurfEdge selection and is excluded from Tab 2 POT%.'
                    : r.settled
                    ? 'Settled and included in Tab 2 POT% profitability tracking.'
                    : undefined
                }
                className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-xs transition ${
                  isSelected
                    ? 'border-emerald-500 bg-emerald-500/15 text-emerald-200'
                    : 'border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700'
                }`}
              >
                <span className="font-bold">R{r.race_number}</span>
                <span className="text-slate-400">{r.distance}m</span>
                {hasOverlay && !isFinalNoPick && (
                  <span className="rounded bg-emerald-500/25 px-1.5 py-0.5 text-[10px] font-bold text-emerald-300">
                    VALUE
                  </span>
                )}
                {r.settled ? (
                  <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-emerald-300">
                    SETTLED
                  </span>
                ) : isFinalNoPick ? (
                  <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-300 ring-1 ring-amber-500/30">
                    FINAL · NO PICK
                  </span>
                ) : isFinished ? (
                  <span className="rounded bg-slate-700 px-1.5 py-0.5 text-[10px] uppercase text-slate-300">
                    {r.status}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      )}

      {!activeRace ? (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-10 text-center">
          <p className="text-sm font-medium text-slate-300">No races loaded for this date &amp; filter.</p>
          <p className="mt-1 text-xs text-slate-500">
            Morning ingestion runs automatically at 8:30 AM AEDT/AEST, or use the Manual CSV/JSON Dropzone above.
          </p>
        </div>
      ) : (
        <>
          {/* Track, Rail, Wind & Bias Summary Banner */}
          <div className="grid gap-3 lg:grid-cols-3">
            <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 lg:col-span-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="rounded-md bg-emerald-500/20 px-2 py-0.5 text-xs font-bold text-emerald-300">
                      {activeRace.tier.toUpperCase()}
                    </span>
                    <span className="text-xs font-semibold text-slate-400">
                      {activeRace.meeting_name} ({activeRace.state}) · Race {activeRace.race_number}
                    </span>
                    {activeRace.pre_jump_analyzed || activeRace.baseline_analyzed ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-semibold text-sky-300">
                        24-PT AUDITED
                      </span>
                    ) : ['final', 'closed', 'paying'].includes((activeRace.status || '').toLowerCase()) && !activeRace.settled ? (
                      <span
                        title="This race finished without a pre-jump TurfEdge selection and is excluded from Tab 2 POT%."
                        className="inline-flex items-center gap-1 rounded-full border border-amber-500/35 bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold text-amber-300"
                      >
                        FINAL · NO PICK (Excluded from POT%)
                      </span>
                    ) : null}
                  </div>
                  <h2 className="mt-1 text-lg font-bold text-white">{activeRace.name}</h2>
                </div>
                <div className="flex flex-wrap gap-2 text-xs">
                  <span className="inline-flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1 text-slate-200">
                    <MapPin className="h-3.5 w-3.5 text-emerald-400" /> {activeRace.distance}m ({activeRace.distance_band})
                  </span>
                  <span className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1 font-medium text-amber-300">
                    {activeRace.track_condition}
                  </span>
                  <span className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1 text-slate-200">
                    Rail: {activeRace.rail_position}
                  </span>
                </div>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-800 pt-2.5 text-xs text-slate-400">
                <Gauge className="h-3.5 w-3.5 text-emerald-400" />
                <span className="font-semibold text-slate-300">Current Meeting Track Bias:</span>
                <span>{activeRace.meeting_bias?.summary || 'No earlier settled races yet today.'}</span>
              </div>
              {activeRace.ai_summary && (
                <p className="mt-2 rounded-lg bg-slate-950/70 px-3 py-2 text-xs text-slate-300">
                  <strong className="text-sky-300">Race Synthesis:</strong> {activeRace.ai_summary}
                </p>
              )}
              {activeRace.results && activeRace.results.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-emerald-500/25 bg-emerald-950/20 px-3 py-2 text-xs">
                  <span className="font-bold text-emerald-300">Official Result:</span>
                  {activeRace.results.slice(0, 4).map((res) => (
                    <span key={res.position} className="rounded bg-slate-900/90 px-2 py-0.5 text-slate-200 tabular-nums">
                      <strong>{res.position === 1 ? '1st' : res.position === 2 ? '2nd' : res.position === 3 ? '3rd' : '4th'}</strong>{' '}
                      #{res.runner_number} {res.name}
                    </span>
                  ))}
                </div>
              )}
            </div>

            <WindCompass weather={activeRace.weather} />
          </div>

          {/* #1 Win Selection Hero Card + Exotics/Quaddie Side Panel */}
          <div className="grid gap-4 lg:grid-cols-12">
            {winSel && (
              <div className="relative overflow-hidden rounded-2xl border border-emerald-500/35 bg-gradient-to-br from-slate-900 via-slate-900 to-emerald-950/35 p-5 lg:col-span-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3">
                    {winSel.silk_url ? (
                      <img src={winSel.silk_url} alt="" className="h-12 w-12 rounded-lg bg-slate-800 object-contain p-1" />
                    ) : (
                      <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-emerald-500/20 text-lg font-black text-emerald-300">
                        #{winSel.runner_number}
                      </div>
                    )}
                    <div>
                      <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-emerald-400">
                        <Trophy className="h-3.5 w-3.5" /> #1 Win Selection
                      </div>
                      <h3 className="text-xl font-black text-white">
                        #{winSel.runner_number} {winSel.name}
                      </h3>
                      <p className="text-xs text-slate-400">
                        Bar {winSel.barrier} · {winSel.jockey} / {winSel.trainer}
                      </p>
                    </div>
                  </div>
                  {winSel.is_value_overlay ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500 px-3 py-1 text-xs font-extrabold text-slate-950 shadow-lg shadow-emerald-500/20">
                      <Flame className="h-3.5 w-3.5" /> VALUE OVERLAY (+{winSel.edge_pct}%)
                    </span>
                  ) : (
                    <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[11px] font-semibold text-slate-400">
                      No Overlay (Live ≤ Rated)
                    </span>
                  )}
                </div>

                <div className="mt-4 grid grid-cols-3 gap-2.5 text-center">
                  <div className="rounded-xl border border-slate-800 bg-slate-950/75 p-3">
                    <div className="text-[10px] font-semibold uppercase text-slate-400">Model Rated</div>
                    <div className="mt-0.5 text-xl font-black text-emerald-400 tabular-nums">
                      ${winSel.rated_price.toFixed(2)}
                    </div>
                    <div className="text-[10px] text-slate-500">{winSel.win_prob_pct}% Win Prob</div>
                  </div>
                  <div className="rounded-xl border border-slate-800 bg-slate-950/75 p-3">
                    <div className="text-[10px] font-semibold uppercase text-slate-400">Opening Price</div>
                    <div className="mt-0.5 text-xl font-bold text-slate-300 tabular-nums">
                      {winSel.opening_price > 0 ? `$${winSel.opening_price.toFixed(2)}` : '—'}
                    </div>
                    <div className="text-[10px] text-slate-500">
                      {winSel.market_fluc.opening_implied_prob.toFixed(1)}% implied
                    </div>
                  </div>
                  <div className="rounded-xl border border-slate-800 bg-slate-950/75 p-3">
                    <div className="text-[10px] font-semibold uppercase text-slate-400">Live Price</div>
                    <div className="mt-0.5 text-xl font-black text-white tabular-nums">
                      {winSel.live_price > 0 ? `$${winSel.live_price.toFixed(2)}` : '—'}
                    </div>
                    <div className="text-[10px] text-slate-500">
                      {winSel.market_fluc.live_implied_prob.toFixed(1)}% implied
                    </div>
                  </div>
                </div>

                <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                  <FlucBadge direction={winSel.market_fluc.direction} pct={winSel.market_fluc.fluc_pct} />
                  <span className="text-xs text-slate-400 tabular-nums">
                    24-Pt Score: <strong className="text-slate-100">{winSel.composite_score.toFixed(1)}</strong>/100
                  </span>
                </div>

                {winSel.ai_note && (
                  <p className="mt-3 text-xs leading-relaxed text-slate-300">{winSel.ai_note}</p>
                )}
              </div>
            )}

            {/* Same Race Multi & Exotics Cards */}
            <div className="grid gap-3 sm:grid-cols-2 lg:col-span-7">
              {/* SRM Combos */}
              <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
                <div className="mb-2.5 flex items-center justify-between">
                  <h3 className="flex items-center gap-1.5 text-sm font-bold text-white">
                    <Layers className="h-4 w-4 text-sky-400" /> Same Race Multi (0.75x Haircut)
                  </h3>
                  <span className="text-[10px] text-slate-400">$10 Unit Stake</span>
                </div>
                <div className="space-y-2.5">
                  {(activeRace.srm_combos || []).map((srm) => {
                    if (srm.skipped) {
                      const badge =
                        srm.skip_badge ||
                        (srm.skip_reason?.includes('< 5')
                          ? 'SKIP · MIN FIELD 5'
                          : srm.skip_reason?.includes('6+ runners')
                          ? 'SKIP · MIN FIELD 6'
                          : 'SKIP · LOW ODDS');
                      return (
                        <div
                          key={srm.leg_count}
                          className="rounded-xl border border-slate-800/80 bg-slate-950/40 p-2.5 opacity-75"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs font-semibold text-slate-400">{srm.label}</span>
                            <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-300 ring-1 ring-amber-500/30">
                              {badge}
                            </span>
                          </div>
                          <p className="mt-1 text-[11px] text-slate-500">
                            {srm.skip_reason || `Skipped: Est. odds $${srm.estimated_odds.toFixed(2)} < $1.80 min (low payout)`}
                          </p>
                          <ul className="mt-1.5 space-y-0.5 text-[11px] text-slate-500">
                            {srm.legs.map((leg, i) => (
                              <li key={i} className="flex justify-between">
                                <span>
                                  #{leg.runner_number} {leg.name} — {leg.market}
                                </span>
                                <span className="tabular-nums">{(leg.prob * 100).toFixed(0)}%</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      );
                    }
                    return (
                      <div key={srm.leg_count} className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-bold text-sky-300">{srm.label}</span>
                          <span className="text-xs font-bold text-emerald-300 tabular-nums">
                            Est. ${srm.estimated_odds.toFixed(2)}{' '}
                            <span className="text-[10px] font-normal text-slate-500">
                              (raw ${srm.raw_harville_odds.toFixed(2)})
                            </span>
                          </span>
                        </div>
                        <ul className="mt-1.5 space-y-0.5 text-[11px] text-slate-300">
                          {srm.legs.map((leg, i) => (
                            <li key={i} className="flex justify-between">
                              <span>
                                #{leg.runner_number} {leg.name} — <strong>{leg.market}</strong>
                              </span>
                              <span className="tabular-nums text-slate-400">{(leg.prob * 100).toFixed(0)}%</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Exotics & Quaddie */}
              <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
                <div className="mb-2.5 flex items-center justify-between">
                  <h3 className="text-sm font-bold text-white">Exotics &amp; Quaddie ($10 Flexi)</h3>
                </div>
                <div className="space-y-2">
                  {(['exacta', 'trifecta', 'first4'] as const).map((k) => {
                    const ex = activeRace.exotics?.[k];
                    if (!ex) return null;
                    if (ex.skipped) {
                      return (
                        <div
                          key={k}
                          className="rounded-xl border border-slate-800/80 bg-slate-950/40 px-3 py-2 opacity-75"
                        >
                          <div className="flex items-center justify-between gap-2 text-xs">
                            <span className="font-semibold text-slate-400">{ex.type}</span>
                            <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-300 ring-1 ring-amber-500/30">
                              SKIP · LOW PAYOUT
                            </span>
                          </div>
                          <p className="mt-1 text-[11px] text-slate-500">
                            {ex.skip_reason || 'Skipped: Projected return below $12.50 minimum.'}
                          </p>
                        </div>
                      );
                    }
                    return (
                      <div key={k} className="rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-2">
                        <div className="flex flex-wrap items-center justify-between gap-1 text-xs">
                          <div className="flex items-center gap-1.5">
                            <span className="font-bold text-amber-300">{ex.type}</span>
                            {ex.structure && ex.structure.includes('Banker') && (
                              <span className="rounded bg-sky-500/15 px-1.5 py-0.2 text-[9px] font-bold uppercase text-sky-300">
                                BANKER
                              </span>
                            )}
                          </div>
                          <span className="text-[11px] text-slate-400 tabular-nums">
                            {ex.combinations} combos · {ex.flexi_pct.toFixed(1)}% flexi
                            {ex.est_anchor_return ? ` · Est ~$${ex.est_anchor_return.toFixed(0)}` : ''}
                          </span>
                        </div>
                        <div className="mt-0.5 font-mono text-xs font-semibold text-slate-100">{ex.display}</div>
                      </div>
                    );
                  })}

                  {activeMeeting?.quaddie_ticket && (
                    <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/20 px-3 py-2.5">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-bold text-emerald-300">
                          Meeting Quaddie (R{activeMeeting.quaddie_ticket.quaddie_races.join('-R')})
                        </span>
                        <span className="text-[11px] text-emerald-200/80 tabular-nums">
                          {activeMeeting.quaddie_ticket.combinations} combos · {activeMeeting.quaddie_ticket.flexi_pct.toFixed(1)}%
                        </span>
                      </div>
                      <div className="mt-1 font-mono text-xs font-bold text-white">
                        {activeMeeting.quaddie_ticket.display}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Ranked Top 10 & Full Field with Expandable 24-Point Audit */}
          <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/70">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-4 py-3">
              <div>
                <h3 className="text-sm font-bold text-white">
                  Ranked Top 10 &amp; Full Field — Same Race Multi Probabilities &amp; 24-Point Audit
                </h3>
                <p className="text-xs text-slate-400">
                  Click any runner row to inspect all 24 weighted factors (missing text fields are locked at neutral 5.0).
                </p>
              </div>
              <span className="text-xs text-slate-400">{activeRace.scored_runners.length} Active Runners</span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-slate-800 bg-slate-950/60 text-[11px] uppercase tracking-wider text-slate-400">
                  <tr>
                    <th className="px-3 py-2.5">Rank</th>
                    <th className="px-3 py-2.5">Runner</th>
                    <th className="px-3 py-2.5">Jockey / Wt</th>
                    <th className="px-3 py-2.5 text-right">24-Pt</th>
                    <th className="px-3 py-2.5 text-right">Rated</th>
                    <th className="px-3 py-2.5 text-right">Open → Live</th>
                    <th className="px-3 py-2.5">Fluc / Overlay</th>
                    <th className="px-3 py-2.5 text-right">Top 2</th>
                    <th className="px-3 py-2.5 text-right">Top 3</th>
                    <th className="px-3 py-2.5 text-right">Top 4</th>
                    <th className="px-3 py-2.5 text-right">Top 10</th>
                    <th className="px-3 py-2.5"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {activeRace.scored_runners.map((runner) => {
                    const isOpen = expandedRunner === runner.runner_number;
                    return (
                      <React.Fragment key={runner.runner_number}>
                        <tr
                          onClick={() => setExpandedRunner(isOpen ? null : runner.runner_number)}
                          className={`cursor-pointer transition hover:bg-slate-800/50 ${
                            isOpen ? 'bg-slate-800/40' : ''
                          }`}
                        >
                          <td className="px-3 py-2.5 font-bold tabular-nums">
                            <span
                              className={`inline-flex h-6 w-6 items-center justify-center rounded-md text-xs ${
                                runner.rank === 1
                                  ? 'bg-emerald-500 text-slate-950'
                                  : runner.rank <= 4
                                  ? 'bg-sky-500/20 text-sky-300'
                                  : 'bg-slate-800 text-slate-400'
                              }`}
                            >
                              {runner.rank}
                            </span>
                          </td>
                          <td className="px-3 py-2.5">
                            <div className="font-semibold text-slate-100">
                              #{runner.runner_number} {runner.name}
                            </div>
                            <div className="text-[11px] text-slate-400">
                              Bar {runner.barrier} · {runner.speedmap_label || 'Midfield'}
                              {runner.last_twenty_starts ? ` · ${runner.last_twenty_starts.slice(-5)}` : ''}
                            </div>
                          </td>
                          <td className="px-3 py-2.5">
                            <div className="text-slate-200">{runner.jockey}</div>
                            <div className="text-[11px] text-slate-400 tabular-nums">
                              {runner.weight_carried.toFixed(1)}kg
                              {runner.apprentice_claim_kg > 0 ? ` (-${runner.apprentice_claim_kg}kg)` : ''}
                            </div>
                          </td>
                          <td className="px-3 py-2.5 text-right font-bold text-slate-100 tabular-nums">
                            {runner.composite_score.toFixed(1)}
                          </td>
                          <td className="px-3 py-2.5 text-right font-semibold text-emerald-400 tabular-nums">
                            ${runner.rated_price.toFixed(2)}
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums text-slate-200">
                            <span className="text-slate-500">
                              {runner.opening_win > 0 ? `$${runner.opening_win.toFixed(2)}` : '—'}
                            </span>{' '}
                            →{' '}
                            <strong className="text-white">
                              {runner.live_win > 0 ? `$${runner.live_win.toFixed(2)}` : '—'}
                            </strong>
                          </td>
                          <td className="px-3 py-2.5">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <FlucBadge direction={runner.market_fluc.direction} pct={runner.market_fluc.fluc_pct} />
                              {runner.is_value_overlay && (
                                <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-bold text-emerald-300 ring-1 ring-emerald-500/40">
                                  OVERLAY
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums text-slate-200">
                            {(runner.probabilities.top2 * 100).toFixed(0)}%
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums text-slate-300">
                            {(runner.probabilities.top3 * 100).toFixed(0)}%
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums text-slate-300">
                            {(runner.probabilities.top4 * 100).toFixed(0)}%
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums text-slate-400">
                            {(runner.probabilities.top10 * 100).toFixed(0)}%
                          </td>
                          <td className="px-3 py-2.5 text-slate-400">
                            {isOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                          </td>
                        </tr>
                        {isOpen && (
                          <tr>
                            <td colSpan={12} className="p-0">
                              <RunnerAuditDrawer runner={runner} />
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
