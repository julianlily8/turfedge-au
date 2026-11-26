import type { MeetingDoc, RaceDoc, SettlementDoc } from './types';
import { FACTOR_DEFINITIONS } from './localScorer23pt';

export interface CoverageAuditStats {
  trackedSettled: number;
  trackedFinished: number;
  missingRaces: string[];
  quaddiesSettled: number;
  quaddiesEligible: number;
  missingQuaddies: string[];
  untrackedNoPick: number;
  upcomingTracked: number;
}

function parseStartTimeMs(ts?: string): number | null {
  if (!ts) return null;
  let cleaned = ts.trim();
  if (cleaned.endsWith(' UTC')) {
    cleaned = cleaned.slice(0, -4).trim().replace(' ', 'T').replace(' +0000', 'Z');
  }
  const ms = Date.parse(cleaned);
  return Number.isNaN(ms) ? null : ms;
}

function isRaceFinished(r: RaceDoc, nowMs: number): boolean {
  const st = (r.status || '').toLowerCase();
  if (st === 'abandoned') return false;
  if (r.settled || ['final', 'settled', 'closed', 'paying'].includes(st)) return true;
  if (r.results && r.results.length > 0) return true;
  const startMs = parseStartTimeMs(r.advertised_start);
  if (startMs !== null && nowMs - startMs > 15 * 60 * 1000) return true;
  return false;
}

export function computeCoverageAudit(
  meetings: MeetingDoc[],
  races: RaceDoc[],
  settlements: SettlementDoc[],
  scopeDate?: string
): CoverageAuditStats {
  const nowMs = Date.now();
  const scopedRaces = scopeDate ? races.filter((r) => r.date === scopeDate) : races;
  const scopedMeetings = scopeDate ? meetings.filter((m) => m.date === scopeDate) : meetings;
  const scopedSettlements = scopeDate ? settlements.filter((s) => s.date === scopeDate) : settlements;

  const settledRaceIds = new Set(
    scopedSettlements.filter((s) => s.kind === 'race' && s.race_id).map((s) => s.race_id as string)
  );
  const settledQuaddieKeys = new Set(
    scopedSettlements
      .filter((s) => s.kind === 'quaddie')
      .map((s) => `${s.date}_${s.meeting_slug}`)
  );

  let trackedFinished = 0;
  let trackedSettled = 0;
  let untrackedNoPick = 0;
  let upcomingTracked = 0;
  const missingRaces: string[] = [];

  const raceById = new Map<string, RaceDoc>();
  scopedRaces.forEach((r) => {
    raceById.set(r.race_id, r);
    const isTracked = Boolean(r.pre_jump_analyzed || r.baseline_analyzed);
    const finished = isRaceFinished(r, nowMs);
    if (isTracked) {
      if (finished) {
        trackedFinished += 1;
        if (settledRaceIds.has(r.race_id)) {
          trackedSettled += 1;
        } else {
          missingRaces.push(`${r.meeting_name} R${r.race_number} (${r.date})`);
        }
      } else {
        upcomingTracked += 1;
      }
    } else if (finished) {
      untrackedNoPick += 1;
    }
  });

  let quaddiesEligible = 0;
  let quaddiesSettled = 0;
  const missingQuaddies: string[] = [];

  scopedMeetings.forEach((m) => {
    const mKey = `${m.date}_${m.meeting_slug}`;
    const hasSettledQ = settledQuaddieKeys.has(mKey);
    const legs = m.quaddie_ticket?.legs || [];
    const all4LegsTrackedAndFinished =
      legs.length === 4 &&
      legs.every((leg) => {
        const r = raceById.get(leg.race_id);
        return Boolean(r && (r.pre_jump_analyzed || r.baseline_analyzed) && isRaceFinished(r, nowMs));
      });

    if (all4LegsTrackedAndFinished || hasSettledQ) {
      quaddiesEligible += 1;
      if (hasSettledQ) {
        quaddiesSettled += 1;
      } else {
        missingQuaddies.push(`${m.display_name || m.name} (${m.date})`);
      }
    }
  });

  return {
    trackedSettled,
    trackedFinished,
    missingRaces,
    quaddiesSettled,
    quaddiesEligible,
    missingQuaddies,
    untrackedNoPick,
    upcomingTracked,
  };
}

export interface ProfitabilityFilters {
  trackCondition: string; // 'All' | 'Firm' | 'Good' | 'Soft' | 'Heavy'
  distanceBand: string;   // 'All' | 'Sprint' | 'Middle' | 'Staying'
  flucDirection: string;  // 'All' | 'Firmer' | 'Steady' | 'Drifter'
  tier: string;           // 'All' | 'Metro' | 'Provincial'
}

export interface BetCategoryMetrics {
  key: string;
  label: string;
  bets: number;
  wins: number;
  strikeRatePct: number;
  totalStaked: number;
  totalReturn: number;
  netProfit: number;
  potPct: number;
}

export interface CumulativePoint {
  label: string;
  date: string;
  winAllCum: number;
  winOverlayCum: number;
  srm2Cum: number;
  exoticsCum: number;
}

export const MIN_SAMPLE_WINS_FOR_TUNING = 100;

export const COMBINED_CATEGORY_OPTIONS: { key: string; shortLabel: string }[] = [
  { key: 'win_all', shortLabel: 'Win (All)' },
  { key: 'win_overlay', shortLabel: 'Win (Overlays)' },
  { key: 'srm_2leg', shortLabel: '2-Leg SRM' },
  { key: 'srm_3leg', shortLabel: '3-Leg SRM' },
  { key: 'srm_4leg', shortLabel: '4-Leg SRM' },
  { key: 'exacta', shortLabel: 'Exacta' },
  { key: 'trifecta', shortLabel: 'Trifecta' },
  { key: 'first4', shortLabel: 'First 4' },
  { key: 'quaddie', shortLabel: 'Quaddie' },
];

export function formatOrdinal(
  pos?: number | null,
  isVoid?: boolean,
  isScratched?: boolean
): string {
  if (isScratched || isVoid) return 'SCR';
  if (pos === null || pos === undefined || pos <= 0 || Number.isNaN(pos)) {
    return 'Unplaced';
  }
  const n = Math.trunc(pos);
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
}

export interface FactorDiagnostic {
  key: string;
  label: string;
  category: string;
  sampleCount: number;
  hasLiveData: boolean;
  avgWinScore: number;
  avgLossScore: number;
  edgeDelta: number;
  highScorePotPct: number | null;
  recommendation: 'Boost Weight' | 'Keep' | 'Trim Weight' | 'Low Sample';
}

export interface LedgerBetDetail {
  betKey: string;
  shortTag: string;
  label: string;
  status: 'W' | 'L' | 'VOID';
  stake: number;
  officialOdds: number | null;
  effectiveReturn: number;
  profit: number;
  isFlexi: boolean;
  flexiPct?: number;
  overrideKey: string;
  hasOverride: boolean;
  comboText: string;
  subText?: string;
}

export function getLedgerBetDetails(
  s: SettlementDoc,
  overrides: OddsOverrideMap
): LedgerBetDetail[] {
  const items: LedgerBetDetail[] = [];

  if (s.win_all) {
    const isVoid = Boolean(
      s.win_all.void ||
        s.win_all.scratched ||
        s.win_all.stake <= 0 ||
        s.win_all.finish_position === null ||
        s.win_all.finish_position === undefined
    );
    const oKey = overrideKey(s.settlement_id, 'win_all');
    const ov = overrides[oKey];
    const ret = isVoid ? 0 : effectiveReturn(s.win_all.won, s.win_all.stake, s.win_all.return, ov);
    const stake = isVoid ? 0 : s.win_all.stake;
    items.push({
      betKey: 'win_all',
      shortTag: 'Win',
      label: 'Win (All)',
      status: isVoid ? 'VOID' : s.win_all.won ? 'W' : 'L',
      stake,
      officialOdds: s.win_all.settled_odds > 0 ? s.win_all.settled_odds : null,
      effectiveReturn: ret,
      profit: Math.round((ret - stake) * 100) / 100,
      isFlexi: false,
      overrideKey: oKey,
      hasOverride: ov !== undefined,
      comboText: `#${s.win_all.runner_number} ${s.win_all.runner_name} (${
        s.win_all.won
          ? '1st'
          : formatOrdinal(s.win_all.finish_position, isVoid, s.win_all.scratched)
      })`,
      subText: s.win_all.betfair_bsp
        ? `Rated $${s.win_all.rated_price?.toFixed(2) ?? '—'} · SP $${s.win_all.settled_odds.toFixed(2)} · BSP $${s.win_all.betfair_bsp.toFixed(2)}`
        : `Rated $${s.win_all.rated_price?.toFixed(2) ?? '—'} · SP $${s.win_all.settled_odds.toFixed(2)}`,
    });
  }

  if (s.win_overlay) {
    const isVoid = Boolean(
      s.win_overlay.void ||
        s.win_overlay.scratched ||
        s.win_overlay.stake <= 0 ||
        s.win_overlay.finish_position === null ||
        s.win_overlay.finish_position === undefined
    );
    const oKey = overrideKey(s.settlement_id, 'win_all');
    const ov = overrides[oKey];
    const ret = isVoid ? 0 : effectiveReturn(s.win_overlay.won, s.win_overlay.stake, s.win_overlay.return, ov);
    const stake = isVoid ? 0 : s.win_overlay.stake;
    items.push({
      betKey: 'win_overlay',
      shortTag: 'Overlay',
      label: 'Win (Value Overlay)',
      status: isVoid ? 'VOID' : s.win_overlay.won ? 'W' : 'L',
      stake,
      officialOdds: s.win_overlay.settled_odds > 0 ? s.win_overlay.settled_odds : null,
      effectiveReturn: ret,
      profit: Math.round((ret - stake) * 100) / 100,
      isFlexi: false,
      overrideKey: oKey,
      hasOverride: ov !== undefined,
      comboText: `#${s.win_overlay.runner_number} ${s.win_overlay.runner_name} (Value Overlay)`,
      subText: `Live $${s.win_overlay.live_price?.toFixed(2) ?? '—'} > Rated $${s.win_overlay.rated_price?.toFixed(2) ?? '—'}`,
    });
  }

  (s.srm || []).forEach((srm) => {
    const betKey = `srm_${srm.leg_count}leg`;
    const isVoid = Boolean(srm.void || srm.stake <= 0);
    const oKey = overrideKey(s.settlement_id, betKey);
    const ov = overrides[oKey];
    const ret = isVoid ? 0 : effectiveReturn(srm.won, srm.stake, srm.return, ov);
    const stake = isVoid ? 0 : srm.stake;
    items.push({
      betKey,
      shortTag: `${srm.leg_count}L`,
      label: `${srm.leg_count}-Leg SRM`,
      status: isVoid ? 'VOID' : srm.won ? 'W' : 'L',
      stake,
      officialOdds: srm.settled_odds > 0 ? srm.settled_odds : null,
      effectiveReturn: ret,
      profit: Math.round((ret - stake) * 100) / 100,
      isFlexi: false,
      overrideKey: oKey,
      hasOverride: ov !== undefined,
      comboText: (srm.legs || []).map((l) => `#${l.runner_number} ${l.name} (${l.market})`).join(' + '),
      subText: `0.75x est. $${srm.settled_odds.toFixed(2)} (raw Harville $${srm.raw_harville_odds.toFixed(2)})`,
    });
  });

  const exoticSpecs: { key: 'exacta' | 'trifecta' | 'first4'; shortTag: string; label: string }[] = [
    { key: 'exacta', shortTag: 'Ex', label: 'Exacta' },
    { key: 'trifecta', shortTag: 'Tri', label: 'Trifecta' },
    { key: 'first4', shortTag: 'F4', label: 'First 4' },
  ];

  exoticSpecs.forEach(({ key, shortTag, label }) => {
    const bet = s[key];
    if (!bet || bet.skipped || bet.stake <= 0) return;
    const isVoid = Boolean(bet.void || bet.stake <= 0);
    const oKey = overrideKey(s.settlement_id, key);
    const ov = overrides[oKey];
    const ret = isVoid ? 0 : effectiveReturn(bet.won, bet.stake, bet.return, ov, bet.flexi_pct);
    const stake = isVoid ? 0 : bet.stake;
    items.push({
      betKey: key,
      shortTag,
      label: `${label} ($10 Flexi)`,
      status: isVoid ? 'VOID' : bet.won ? 'W' : 'L',
      stake,
      officialOdds: bet.dividend && bet.dividend > 0 ? bet.dividend : null,
      effectiveReturn: ret,
      profit: Math.round((ret - stake) * 100) / 100,
      isFlexi: true,
      flexiPct: bet.flexi_pct,
      overrideKey: oKey,
      hasOverride: ov !== undefined,
      comboText: bet.display,
      subText: `${bet.combinations} combos · ${bet.flexi_pct.toFixed(1)}% flexi${
        bet.dividend ? ` · Official Div $${bet.dividend.toFixed(2)}` : ''
      }`,
    });
  });

  if (s.quaddie) {
    const isVoid = Boolean(s.quaddie.void || s.quaddie.stake <= 0);
    const oKey = overrideKey(s.settlement_id, 'quaddie');
    const ov = overrides[oKey];
    const ret = isVoid
      ? 0
      : effectiveReturn(s.quaddie.won, s.quaddie.stake, s.quaddie.return, ov, s.quaddie.flexi_pct);
    const stake = isVoid ? 0 : s.quaddie.stake;
    items.push({
      betKey: 'quaddie',
      shortTag: 'Quad',
      label: 'Quaddie ($10 Flexi)',
      status: isVoid ? 'VOID' : s.quaddie.won ? 'W' : 'L',
      stake,
      officialOdds: s.quaddie.dividend && s.quaddie.dividend > 0 ? s.quaddie.dividend : null,
      effectiveReturn: ret,
      profit: Math.round((ret - stake) * 100) / 100,
      isFlexi: true,
      flexiPct: s.quaddie.flexi_pct,
      overrideKey: oKey,
      hasOverride: ov !== undefined,
      comboText: s.quaddie.display,
      subText: `${s.quaddie.legs_won ?? 0}/4 legs · ${s.quaddie.combinations} combos · ${s.quaddie.flexi_pct.toFixed(
        1
      )}% flexi${s.quaddie.dividend ? ` · Official Div $${s.quaddie.dividend.toFixed(2)}` : ''}`,
    });
  }

  return items;
}

const OVERRIDES_STORAGE_KEY = 'turfedge_actual_odds_overrides_v1';

export type OddsOverrideMap = Record<string, number>;

export function loadOddsOverrides(): OddsOverrideMap {
  try {
    const raw = localStorage.getItem(OVERRIDES_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as OddsOverrideMap) : {};
  } catch {
    return {};
  }
}

export function saveOddsOverrides(map: OddsOverrideMap): void {
  try {
    localStorage.setItem(OVERRIDES_STORAGE_KEY, JSON.stringify(map));
  } catch {
    // ignore storage quota issues
  }
}

export function overrideKey(settlementId: string, betKey: string): string {
  return `${settlementId}::${betKey}`;
}

export function filterSettlements(
  settlements: SettlementDoc[],
  filters: ProfitabilityFilters
): SettlementDoc[] {
  return settlements.filter((s) => {
    if (filters.tier !== 'All' && s.tier !== filters.tier) return false;
    if (filters.trackCondition !== 'All' && s.track_condition_band !== filters.trackCondition) return false;
    // Quaddies span multiple distances/flucs, include them unless distance/fluc filter explicitly excludes
    if (s.kind === 'race') {
      if (filters.distanceBand !== 'All' && s.distance_band !== filters.distanceBand) return false;
      if (filters.flucDirection !== 'All' && s.fluc_direction !== filters.flucDirection) return false;
    }
    return true;
  });
}

function effectiveReturn(
  won: boolean,
  stake: number,
  defaultReturn: number,
  overrideOdds?: number,
  flexiPct?: number
): number {
  if (!won) return 0;
  if (overrideOdds && overrideOdds > 0) {
    if (flexiPct !== undefined) {
      // Exotic/Quaddie dividend override scaled by flexi %
      return Math.round(overrideOdds * (flexiPct / 100) * 100) / 100;
    }
    return Math.round(stake * overrideOdds * 100) / 100;
  }
  return defaultReturn;
}

export function computeProfitability(
  settlements: SettlementDoc[],
  overrides: OddsOverrideMap,
  selectedCombinedKeys: string[] = ['win_all']
): {
  categories: BetCategoryMetrics[];
  overall: BetCategoryMetrics;
  cumulative: CumulativePoint[];
  factorDiagnostics: FactorDiagnostic[];
  totalWinCount: number;
} {
  const buckets: Record<string, { label: string; bets: number; wins: number; staked: number; ret: number }> = {
    win_all: { label: 'Win (All)', bets: 0, wins: 0, staked: 0, ret: 0 },
    win_overlay: { label: 'Win (Value Overlays Only)', bets: 0, wins: 0, staked: 0, ret: 0 },
    srm_2leg: { label: 'Same Race Multi (2-Leg · 0.75x)', bets: 0, wins: 0, staked: 0, ret: 0 },
    srm_3leg: { label: 'Same Race Multi (3-Leg · 0.75x)', bets: 0, wins: 0, staked: 0, ret: 0 },
    srm_4leg: { label: 'Same Race Multi (4-Leg · 0.75x)', bets: 0, wins: 0, staked: 0, ret: 0 },
    exacta: { label: 'Exacta ($10 Flexi)', bets: 0, wins: 0, staked: 0, ret: 0 },
    trifecta: { label: 'Trifecta ($10 Flexi)', bets: 0, wins: 0, staked: 0, ret: 0 },
    first4: { label: 'First 4 ($10 Flexi)', bets: 0, wins: 0, staked: 0, ret: 0 },
    quaddie: { label: 'Quaddie ($10 Flexi)', bets: 0, wins: 0, staked: 0, ret: 0 },
  };

  const add = (key: string, stake: number, won: boolean, ret: number, isVoid?: boolean) => {
    const b = buckets[key];
    if (!b || isVoid || stake <= 0) return;
    b.bets += 1;
    if (won) b.wins += 1;
    b.staked += stake;
    b.ret += ret;
  };

  // Chronological order for cumulative chart
  const ordered = [...settlements].sort(
    (a, b) => (a.date || '').localeCompare(b.date || '') || (a.race_number || 0) - (b.race_number || 0)
  );

  let winAllCum = 0;
  let winOverlayCum = 0;
  let srm2Cum = 0;
  let exoticsCum = 0;
  let totalWinCount = 0;
  const cumulative: CumulativePoint[] = [];

  // Factor attribution accumulators
  const factorStats: Record<
    string,
    { winScores: number[]; lossScores: number[]; highStaked: number; highRet: number }
  > = {};
  FACTOR_DEFINITIONS.forEach((f) => {
    factorStats[f.key] = { winScores: [], lossScores: [], highStaked: 0, highRet: 0 };
  });

  ordered.forEach((s) => {
    const winVoid = Boolean(
      s.win_all &&
        (s.win_all.void ||
          s.win_all.scratched ||
          s.win_all.stake <= 0 ||
          s.win_all.finish_position === null ||
          s.win_all.finish_position === undefined)
    );

    if (s.win_all && !winVoid) {
      const ov = overrides[overrideKey(s.settlement_id, 'win_all')];
      const ret = effectiveReturn(s.win_all.won, s.win_all.stake, s.win_all.return, ov);
      add('win_all', s.win_all.stake, s.win_all.won, ret, false);
      winAllCum = Math.round((winAllCum + ret - s.win_all.stake) * 100) / 100;
      if (s.win_all.won) totalWinCount += 1;

      // Factor attribution on non-void #1 Win selections
      if (s.top_runner_factor_scores) {
        Object.entries(s.top_runner_factor_scores).forEach(([fk, score]) => {
          const st = factorStats[fk];
          if (!st) return;
          if (s.win_all!.won) st.winScores.push(score);
          else st.lossScores.push(score);
          if (score >= 6.8) {
            st.highStaked += s.win_all!.stake;
            st.highRet += ret;
          }
        });
      }
    }

    if (s.win_overlay && !winVoid) {
      const ov = overrides[overrideKey(s.settlement_id, 'win_all')];
      const ret = effectiveReturn(s.win_overlay.won, s.win_overlay.stake, s.win_overlay.return, ov);
      add('win_overlay', s.win_overlay.stake, s.win_overlay.won, ret, s.win_overlay.void);
      winOverlayCum = Math.round((winOverlayCum + ret - s.win_overlay.stake) * 100) / 100;
    }

    (s.srm || []).forEach((srm) => {
      if (srm.void || srm.stake <= 0) return;
      const key = `srm_${srm.leg_count}leg`;
      const ov = overrides[overrideKey(s.settlement_id, key)];
      const ret = effectiveReturn(srm.won, srm.stake, srm.return, ov);
      add(key, srm.stake, srm.won, ret, srm.void);
      if (srm.leg_count === 2) {
        srm2Cum = Math.round((srm2Cum + ret - srm.stake) * 100) / 100;
      }
    });

    (['exacta', 'trifecta', 'first4'] as const).forEach((exKey) => {
      const bet = s[exKey];
      if (!bet || bet.void || bet.skipped || bet.stake <= 0) return;
      const ov = overrides[overrideKey(s.settlement_id, exKey)];
      const ret = effectiveReturn(bet.won, bet.stake, bet.return, ov, bet.flexi_pct);
      add(exKey, bet.stake, bet.won, ret, bet.void);
      exoticsCum = Math.round((exoticsCum + ret - bet.stake) * 100) / 100;
    });

    if (s.quaddie && !s.quaddie.void && s.quaddie.stake > 0) {
      const ov = overrides[overrideKey(s.settlement_id, 'quaddie')];
      const ret = effectiveReturn(s.quaddie.won, s.quaddie.stake, s.quaddie.return, ov, s.quaddie.flexi_pct);
      add('quaddie', s.quaddie.stake, s.quaddie.won, ret, s.quaddie.void);
      exoticsCum = Math.round((exoticsCum + ret - s.quaddie.stake) * 100) / 100;
    }

    cumulative.push({
      label: s.kind === 'quaddie' ? `${s.meeting_name} Q` : `${s.meeting_name.slice(0, 6)} R${s.race_number}`,
      date: s.date,
      winAllCum,
      winOverlayCum,
      srm2Cum,
      exoticsCum,
    });
  });

  const toMetrics = (key: string, b: { label: string; bets: number; wins: number; staked: number; ret: number }): BetCategoryMetrics => {
    const net = Math.round((b.ret - b.staked) * 100) / 100;
    const pot = b.staked > 0 ? Math.round((net / b.staked) * 1000) / 10 : 0;
    const sr = b.bets > 0 ? Math.round((b.wins / b.bets) * 1000) / 10 : 0;
    return {
      key,
      label: b.label,
      bets: b.bets,
      wins: b.wins,
      strikeRatePct: sr,
      totalStaked: Math.round(b.staked * 100) / 100,
      totalReturn: Math.round(b.ret * 100) / 100,
      netProfit: net,
      potPct: pot,
    };
  };

  const categories = Object.entries(buckets).map(([k, b]) => toMetrics(k, b));

  // Combined rollup respects user-selected bet category checkboxes (defaults to ['win_all'])
  const selectedSet = new Set(selectedCombinedKeys);
  const overallTotals = categories
    .filter((c) => selectedSet.has(c.key))
    .reduce(
      (acc, c) => ({
        bets: acc.bets + c.bets,
        wins: acc.wins + c.wins,
        staked: acc.staked + c.totalStaked,
        ret: acc.ret + c.totalReturn,
      }),
      { bets: 0, wins: 0, staked: 0, ret: 0 }
    );
  const overall = toMetrics('overall', { label: 'Selected Portfolios Combined', ...overallTotals });

  // Strict delta threshold + 100-win minimum sample-size guard:
  // Force 'Low Sample' whenever total settled wins < 100 so small-sample noise never shows Boost/Keep/Trim.
  // Always return ALL 23 factors with an explicit hasLiveData flag (never silently dropping neutral 5.0 factors).
  const factorDiagnostics: FactorDiagnostic[] = FACTOR_DEFINITIONS.map((f) => {
    const st = factorStats[f.key];
    const sampleCount = st.winScores.length + st.lossScores.length;
    const hasLiveData = sampleCount > 0;
    const avgWin = st.winScores.length
      ? st.winScores.reduce((a, b) => a + b, 0) / st.winScores.length
      : hasLiveData
      ? 0
      : 5.0;
    const avgLoss = st.lossScores.length
      ? st.lossScores.reduce((a, b) => a + b, 0) / st.lossScores.length
      : hasLiveData
      ? 0
      : 5.0;
    const edgeDelta = hasLiveData ? Math.round((avgWin - avgLoss) * 100) / 100 : 0;
    const highScorePotPct =
      st.highStaked > 0 ? Math.round(((st.highRet - st.highStaked) / st.highStaked) * 1000) / 10 : null;

    let recommendation: FactorDiagnostic['recommendation'] = 'Low Sample';
    if (
      !hasLiveData ||
      totalWinCount < MIN_SAMPLE_WINS_FOR_TUNING ||
      st.winScores.length === 0 ||
      st.lossScores.length === 0
    ) {
      recommendation = 'Low Sample';
    } else if (edgeDelta >= 0.5) {
      recommendation = 'Boost Weight';
    } else if (edgeDelta <= -0.5) {
      recommendation = 'Trim Weight';
    } else {
      recommendation = 'Keep';
    }

    return {
      key: f.key,
      label: f.label,
      category: f.category,
      sampleCount,
      hasLiveData,
      avgWinScore: Math.round(avgWin * 100) / 100,
      avgLossScore: Math.round(avgLoss * 100) / 100,
      edgeDelta,
      highScorePotPct,
      recommendation,
    };
  }).sort((a, b) => Number(b.hasLiveData) - Number(a.hasLiveData) || b.edgeDelta - a.edgeDelta);

  return { categories, overall, cumulative, factorDiagnostics, totalWinCount };
}
