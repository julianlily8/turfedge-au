export interface AuditFactor {
  key: string;
  label: string;
  category: string;
  weight: number;
  score: number;
  weighted_points: number;
  has_data: boolean;
  reason: string;
}

export interface MarketFluc {
  opening_win: number;
  live_win: number;
  opening_implied_prob: number;
  live_implied_prob: number;
  implied_prob_delta_pct: number;
  fluc_pct: number;
  direction: 'Firmer' | 'Drifter' | 'Steady';
}

export interface RunnerProbabilities {
  win: number;
  top2: number;
  top3: number;
  top4: number;
  top10: number;
}

export interface ScoredRunner {
  entrant_id?: string;
  rank: number;
  runner_number: number;
  name: string;
  barrier: number;
  is_scratched?: boolean;
  jockey: string;
  trainer: string;
  apprentice_claim_kg: number;
  weight_allocated: number;
  weight_carried: number;
  gear: string;
  sire: string;
  dam: string;
  last_twenty_starts: string;
  silk_url?: string;
  opening_win: number;
  live_win: number;
  live_place?: number;
  rated_price: number;
  is_value_overlay: boolean;
  edge_pct: number;
  speedmap_label: string;
  settling_lengths?: number;
  composite_score: number;
  market_fluc: MarketFluc;
  probabilities: RunnerProbabilities;
  audit_23pt: AuditFactor[];
  ai_note?: string;
}

export interface WinSelection {
  runner_number: number;
  name: string;
  barrier: number;
  jockey: string;
  trainer: string;
  silk_url?: string;
  composite_score: number;
  win_prob_pct: number;
  rated_price: number;
  opening_price: number;
  live_price: number;
  is_value_overlay: boolean;
  edge_pct: number;
  market_fluc: MarketFluc;
  ai_note?: string;
}

export interface Top10Entry {
  rank: number;
  runner_number: number;
  name: string;
  barrier: number;
  jockey: string;
  composite_score: number;
  rated_price: number;
  live_price: number;
  is_value_overlay: boolean;
  fluc_direction: 'Firmer' | 'Drifter' | 'Steady';
  fluc_pct: number;
  probabilities: RunnerProbabilities;
}

export interface SrmLeg {
  runner_number: number;
  name: string;
  market: string;
  prob: number;
}

export interface SrmCombo {
  leg_count: number;
  label: string;
  legs: SrmLeg[];
  raw_harville_odds: number;
  haircut_factor: number;
  estimated_odds: number;
  unit_stake: number;
}

export interface ExoticTicket {
  type: string;
  legs: number[][];
  combinations: number;
  flexi_pct: number;
  unit_stake: number;
  display: string;
}

export interface WeatherInfo {
  source: string;
  temperature_c: number | null;
  rain_chance_pct: number;
  rain_expected_mm: number;
  wind_speed_kmh: number;
  wind_from_deg: number;
  wind_compass: string;
  home_straight_bearing: number;
  headwind_kmh: number;
  crosswind_kmh: number;
  effect: string;
  pace_bias: string;
  summary: string;
}

export interface MeetingBias {
  sample_size: number;
  pace_bias: string;
  barrier_bias: string;
  summary: string;
}

export interface QuaddieTicket {
  meeting_slug: string;
  meeting_name: string;
  date: string;
  quaddie_races: number[];
  legs: {
    race_id: string;
    race_number: number;
    race_name: string;
    runner_numbers: number[];
    runner_names: string[];
  }[];
  combinations: number;
  flexi_pct: number;
  unit_stake: number;
  display: string;
}

export interface MeetingDoc {
  meeting_id?: string;
  meeting_slug: string;
  date: string;
  name: string;
  display_name: string;
  state: string;
  tier: 'Metro' | 'Provincial' | 'Country';
  track_condition: string;
  track_condition_band: string;
  rail_position: string;
  quaddie_races: number[];
  quaddie_ticket?: QuaddieTicket | null;
  weather: WeatherInfo;
  meeting_bias: MeetingBias;
  race_count: number;
}

export interface RaceResultEntry {
  position: number;
  runner_number: number;
  name: string;
  barrier: number;
  margin_length: number;
}

export interface RaceDoc {
  race_id: string;
  date: string;
  meeting_slug: string;
  meeting_name: string;
  state: string;
  tier: 'Metro' | 'Provincial' | 'Country';
  race_number: number;
  name: string;
  status: string;
  advertised_start: string;
  distance: number;
  distance_band: 'Sprint' | 'Middle' | 'Staying';
  track_condition: string;
  track_condition_band: 'Firm' | 'Good' | 'Soft' | 'Heavy' | 'Synthetic';
  rail_position: string;
  track_direction?: string;
  track_home_straight?: number;
  weather: WeatherInfo;
  meeting_bias: MeetingBias;
  scored_runners: ScoredRunner[];
  win_selection: WinSelection | null;
  top_10: Top10Entry[];
  srm_combos: SrmCombo[];
  exotics: {
    exacta: ExoticTicket | null;
    trifecta: ExoticTicket | null;
    first4: ExoticTicket | null;
  };
  results?: RaceResultEntry[];
  ai_summary?: string;
  ai_model?: string | null;
  pre_jump_analyzed?: boolean;
  settled?: boolean;
  is_manual_upload?: boolean;
}

export interface SettledWinBet {
  category: string;
  runner_number: number;
  runner_name: string;
  stake: number;
  won: boolean;
  void?: boolean;
  scratched?: boolean;
  finish_position?: number | null;
  settled_odds: number;
  tote_sp?: number | null;
  betfair_bsp?: number | null;
  actual_odds_override?: number | null;
  return: number;
  profit: number;
  bsp_return?: number | null;
  bsp_profit?: number | null;
  is_value_overlay: boolean;
  rated_price?: number;
  live_price?: number;
  fluc_direction: 'Firmer' | 'Drifter' | 'Steady';
  fluc_pct: number;
}

export interface SettledSrmBet {
  category: string;
  leg_count: number;
  label: string;
  legs: SrmLeg[];
  stake: number;
  won: boolean;
  void?: boolean;
  raw_harville_odds: number;
  haircut_factor: number;
  settled_odds: number;
  actual_odds_override?: number | null;
  return: number;
  profit: number;
}

export interface SettledExoticBet {
  category: string;
  display: string;
  combinations: number;
  flexi_pct: number;
  legs_won?: number;
  stake: number;
  won: boolean;
  void?: boolean;
  dividend: number | null;
  actual_odds_override?: number | null;
  return: number;
  profit: number;
}

export interface SettlementDoc {
  settlement_id: string;
  kind: 'race' | 'quaddie';
  race_id?: string;
  date: string;
  meeting_slug: string;
  meeting_name: string;
  tier: 'Metro' | 'Provincial' | 'Country';
  race_number?: number;
  race_name?: string;
  track_condition: string;
  track_condition_band: string;
  distance?: number;
  distance_band: string;
  fluc_direction: string;
  top4_order?: number[];
  win_all?: SettledWinBet | null;
  win_overlay?: SettledWinBet | null;
  srm?: SettledSrmBet[];
  exacta?: SettledExoticBet | null;
  trifecta?: SettledExoticBet | null;
  first4?: SettledExoticBet | null;
  quaddie?: SettledExoticBet | null;
  top_runner_factor_scores?: Record<string, number>;
  bsp_reconciled?: boolean;
}
