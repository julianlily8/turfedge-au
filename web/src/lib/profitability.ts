import type { SettlementDoc } from './types';
import { FACTOR_DEFINITIONS } from './localScorer23pt';

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

export interface FactorDiagnostic {
  key: string;
  label: string;
  category: string;
  sampleCount: number;
  avgWinScore: number;
  avgLossScore: number;
  edgeDelta: number;
  highScorePotPct: number | null;
  recommendation: 'Boost Weight' | 'Keep' | 'Trim Weight' | 'Low Sample';
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
  overrides: OddsOverrideMap
): {
  categories: BetCategoryMetrics[];
  overall: BetCategoryMetrics;
  cumulative: CumulativePoint[];
  factorDiagnostics: FactorDiagnostic[];
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

  const add = (key: string, stake: number, won: boolean, ret: number) => {
    const b = buckets[key];
    if (!b) return;
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
    if (s.win_all) {
      const ov = overrides[overrideKey(s.settlement_id, 'win_all')];
      const ret = effectiveReturn(s.win_all.won, s.win_all.stake, s.win_all.return, ov);
      add('win_all', s.win_all.stake, s.win_all.won, ret);
      winAllCum = Math.round((winAllCum + ret - s.win_all.stake) * 100) / 100;

      // Factor attribution on #1 Win selections
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

    if (s.win_overlay) {
      const ov = overrides[overrideKey(s.settlement_id, 'win_all')];
      const ret = effectiveReturn(s.win_overlay.won, s.win_overlay.stake, s.win_overlay.return, ov);
      add('win_overlay', s.win_overlay.stake, s.win_overlay.won, ret);
      winOverlayCum = Math.round((winOverlayCum + ret - s.win_overlay.stake) * 100) / 100;
    }

    (s.srm || []).forEach((srm) => {
      const key = `srm_${srm.leg_count}leg`;
      const ov = overrides[overrideKey(s.settlement_id, key)];
      const ret = effectiveReturn(srm.won, srm.stake, srm.return, ov);
      add(key, srm.stake, srm.won, ret);
      if (srm.leg_count === 2) {
        srm2Cum = Math.round((srm2Cum + ret - srm.stake) * 100) / 100;
      }
    });

    (['exacta', 'trifecta', 'first4'] as const).forEach((exKey) => {
      const bet = s[exKey];
      if (!bet) return;
      const ov = overrides[overrideKey(s.settlement_id, exKey)];
      const ret = effectiveReturn(bet.won, bet.stake, bet.return, ov, bet.flexi_pct);
      add(exKey, bet.stake, bet.won, ret);
      exoticsCum = Math.round((exoticsCum + ret - bet.stake) * 100) / 100;
    });

    if (s.quaddie) {
      const ov = overrides[overrideKey(s.settlement_id, 'quaddie')];
      const ret = effectiveReturn(s.quaddie.won, s.quaddie.stake, s.quaddie.return, ov, s.quaddie.flexi_pct);
      add('quaddie', s.quaddie.stake, s.quaddie.won, ret);
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

  // Overall (excluding win_overlay double-count since win_overlay is a subset of win_all)
  const overallTotals = categories
    .filter((c) => c.key !== 'win_overlay')
    .reduce(
      (acc, c) => ({
        bets: acc.bets + c.bets,
        wins: acc.wins + c.wins,
        staked: acc.staked + c.totalStaked,
        ret: acc.ret + c.totalReturn,
      }),
      { bets: 0, wins: 0, staked: 0, ret: 0 }
    );
  const overall = toMetrics('overall', { label: 'All Portfolios Combined', ...overallTotals });

  const factorDiagnostics: FactorDiagnostic[] = FACTOR_DEFINITIONS.map((f) => {
    const st = factorStats[f.key];
    const sampleCount = st.winScores.length + st.lossScores.length;
    const avgWin = st.winScores.length ? st.winScores.reduce((a, b) => a + b, 0) / st.winScores.length : 0;
    const avgLoss = st.lossScores.length ? st.lossScores.reduce((a, b) => a + b, 0) / st.lossScores.length : 0;
    const edgeDelta = Math.round((avgWin - avgLoss) * 100) / 100;
    const highScorePotPct =
      st.highStaked > 0 ? Math.round(((st.highRet - st.highStaked) / st.highStaked) * 1000) / 10 : null;
    let recommendation: FactorDiagnostic['recommendation'] = 'Keep';
    if (sampleCount < 3) recommendation = 'Low Sample';
    else if (edgeDelta >= 0.45 && (highScorePotPct === null || highScorePotPct >= 5)) recommendation = 'Boost Weight';
    else if (edgeDelta <= -0.45 || (highScorePotPct !== null && highScorePotPct <= -20)) recommendation = 'Trim Weight';

    return {
      key: f.key,
      label: f.label,
      category: f.category,
      sampleCount,
      avgWinScore: Math.round(avgWin * 100) / 100,
      avgLossScore: Math.round(avgLoss * 100) / 100,
      edgeDelta,
      highScorePotPct,
      recommendation,
    };
  }).sort((a, b) => b.edgeDelta - a.edgeDelta);

  return { categories, overall, cumulative, factorDiagnostics };
}
