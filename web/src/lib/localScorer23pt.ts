import type { AuditFactor, RaceDoc, ScoredRunner, SrmCombo, Top10Entry } from './types';

export const SRM_CORRELATION_HAIRCUT = 0.75;
export const NEUTRAL_SCORE = 5.0;
export const NO_DATA_LABEL = 'No data available';

export const FACTOR_DEFINITIONS: { key: string; label: string; weight: number; category: string }[] = [
  { key: 'weather_ground', label: 'Weather & Ground Forecast', weight: 5.0, category: 'Track & Conditions' },
  { key: 'rail_placement', label: 'Rail Placement', weight: 4.0, category: 'Track & Conditions' },
  { key: 'wind_impact', label: 'Wind Speed & Direction Impact', weight: 3.0, category: 'Track & Conditions' },
  { key: 'current_meeting_bias', label: 'Current Meeting Track Bias', weight: 5.0, category: 'Track & Conditions' },
  { key: 'historical_track_bias', label: 'Historical Race & Track Bias', weight: 3.5, category: 'Track & Conditions' },
  { key: 'adjusted_sectionals', label: 'Adjusted Sectionals', weight: 8.0, category: 'Performance & Speed' },
  { key: 'recent_trials', label: 'Recent Trials & Jumpouts', weight: 4.0, category: 'Performance & Speed' },
  { key: 'pedigree_suitability', label: 'Pedigree & Sire/Dam Suitability', weight: 3.5, category: 'Profile & Fitness' },
  { key: 'jockey_form', label: 'Jockey Recent Form', weight: 5.0, category: 'Connections' },
  { key: 'trainer_form', label: 'Trainer Recent Form', weight: 5.0, category: 'Connections' },
  { key: 'weight_claims', label: 'Weight & Apprentice Claims', weight: 4.0, category: 'Profile & Fitness' },
  { key: 'jockey_track_familiarity', label: 'Jockey Track Familiarity', weight: 3.0, category: 'Connections' },
  { key: 'speedmap_pace', label: 'Speedmap & Pace Position', weight: 7.0, category: 'Performance & Speed' },
  { key: 'days_since_run', label: 'Days Since Last Run / Trial', weight: 4.0, category: 'Profile & Fitness' },
  { key: 'gear_changes', label: 'Gear Changes', weight: 3.0, category: 'Profile & Fitness' },
  { key: 'injury_history', label: 'Injury & Vet History', weight: 2.5, category: 'Qualitative & Intel' },
  { key: 'stewards_comments', label: 'Stewards Comments', weight: 3.5, category: 'Qualitative & Intel' },
  { key: 'trainer_jockey_commentary', label: 'Trainer & Jockey Commentary', weight: 3.0, category: 'Qualitative & Intel' },
  { key: 'target_race_prep', label: 'Target-Race Preparation Weighting', weight: 4.0, category: 'Profile & Fitness' },
  { key: 'youtube_transcripts', label: 'YouTube Preview Transcripts', weight: 2.5, category: 'Qualitative & Intel' },
  { key: 'market_fluc', label: 'Live Market Firming / Drifting %', weight: 8.5, category: 'Market' },
  { key: 'barrier_geometry', label: 'Barrier Draw & Track Geometry', weight: 4.0, category: 'Track & Conditions' },
  { key: 'class_track_distance', label: 'Class, Track & Distance Record', weight: 5.0, category: 'Performance & Speed' },
];

export interface ManualRunnerInput {
  runner_number: number;
  name: string;
  barrier: number;
  weight?: number;
  claim_kg?: number;
  jockey?: string;
  trainer?: string;
  opening_win?: number;
  live_win?: number;
  speedmap?: string;
  last_five?: string;
  gear?: string;
  sire?: string;
  days_since_run?: number;
  stewards_comment?: string;
  trial_note?: string;
  vet_note?: string;
  trainer_comment?: string;
}

export interface ManualRaceInput {
  meeting_name: string;
  tier?: 'Metro' | 'Provincial';
  state?: string;
  date?: string;
  race_number: number;
  race_name: string;
  distance: number;
  track_condition?: string;
  rail_position?: string;
  wind_speed_kmh?: number;
  wind_effect?: string;
  runners: ManualRunnerInput[];
}

const clamp = (v: number, lo = 0.5, hi = 10) => Math.round(Math.max(lo, Math.min(hi, v)) * 100) / 100;

function computeFluc(opening: number, live: number) {
  if (opening <= 1 || live <= 1) {
    return {
      opening_win: opening,
      live_win: live,
      opening_implied_prob: 0,
      live_implied_prob: 0,
      implied_prob_delta_pct: 0,
      fluc_pct: 0,
      direction: 'Steady' as const,
    };
  }
  const pOpen = 1 / opening;
  const pLive = 1 / live;
  const deltaPp = (pLive - pOpen) * 100;
  const relPct = ((pLive - pOpen) / pOpen) * 100;
  const direction =
    relPct >= 6 || deltaPp >= 1.8
      ? ('Firmer' as const)
      : relPct <= -6 || deltaPp <= -1.8
      ? ('Drifter' as const)
      : ('Steady' as const);
  return {
    opening_win: Math.round(opening * 100) / 100,
    live_win: Math.round(live * 100) / 100,
    opening_implied_prob: Math.round(pOpen * 10000) / 100,
    live_implied_prob: Math.round(pLive * 10000) / 100,
    implied_prob_delta_pct: Math.round(deltaPp * 100) / 100,
    fluc_pct: Math.round(relPct * 10) / 10,
    direction,
  };
}

function scoreLastFive(form: string): number {
  const digits = (form || '').replace(/[^0-9]/g, '').slice(-4).split('');
  if (!digits.length) return NEUTRAL_SCORE;
  let pts = 0;
  digits.forEach((d) => {
    const pos = d === '0' ? 10 : Number(d);
    if (pos === 1) pts += 9.2;
    else if (pos === 2) pts += 7.8;
    else if (pos === 3) pts += 6.8;
    else if (pos <= 5) pts += 5.4;
    else pts += 3.5;
  });
  return clamp(pts / digits.length);
}

export function scoreManualRace(input: ManualRaceInput): RaceDoc {
  const runnersIn = input.runners.filter((r) => r.name);
  const fieldSize = Math.max(runnersIn.length, 1);
  const avgWeight =
    runnersIn.reduce((acc, r) => acc + (Number(r.weight) || 57) - (Number(r.claim_kg) || 0), 0) / fieldSize;
  const cond = input.track_condition || 'Good4';
  const condBand = cond.toLowerCase().includes('heavy')
    ? 'Heavy'
    : cond.toLowerCase().includes('soft')
    ? 'Soft'
    : cond.toLowerCase().includes('firm')
    ? 'Firm'
    : 'Good';
  const distBand = input.distance <= 1200 ? 'Sprint' : input.distance <= 1600 ? 'Middle' : 'Staying';
  const railOut = Number((input.rail_position || '').match(/(\d+)/)?.[1] || 0);

  const scored: ScoredRunner[] = runnersIn.map((r) => {
    const open = Number(r.opening_win) || Number(r.live_win) || 8.0;
    const live = Number(r.live_win) || open;
    const fluc = computeFluc(open, live);
    const barPct = (r.barrier || 5) / fieldSize;
    const sm = (r.speedmap || '').toLowerCase();
    const onPace = sm.includes('lead') || sm.includes('pace') || sm.includes('handy');
    const carried = Math.max(48, (Number(r.weight) || 57) - (Number(r.claim_kg) || 0));
    const formScore = scoreLastFive(r.last_five || '');

    const map: Record<string, { score: number; has_data: boolean; reason: string }> = {
      weather_ground: { score: condBand === 'Good' ? 5.6 : 5.2, has_data: true, reason: `${cond} track rating.` },
      rail_placement: {
        score: railOut >= 5 ? (onPace && barPct <= 0.5 ? 7.2 : 4.6) : 5.4,
        has_data: true,
        reason: `Rail ${input.rail_position || 'True'}.`,
      },
      wind_impact: {
        score: (input.wind_speed_kmh || 0) >= 15 ? (onPace ? 4.8 : 6.0) : 5.0,
        has_data: Boolean(input.wind_speed_kmh),
        reason: input.wind_speed_kmh ? `${input.wind_speed_kmh} km/h (${input.wind_effect || 'Breeze'})` : 'Calm/unspecified wind.',
      },
      current_meeting_bias: { score: NEUTRAL_SCORE, has_data: false, reason: 'Manual upload — no live bias sample.' },
      historical_track_bias: { score: onPace ? 6.0 : 5.2, has_data: true, reason: 'Standard Australian track baseline.' },
      adjusted_sectionals: { score: formScore, has_data: Boolean(r.last_five), reason: r.last_five ? `Recent form '${r.last_five}'.` : NO_DATA_LABEL },
      recent_trials: r.trial_note
        ? { score: 6.8, has_data: true, reason: r.trial_note }
        : { score: NEUTRAL_SCORE, has_data: false, reason: NO_DATA_LABEL },
      pedigree_suitability: r.sire
        ? { score: 5.8, has_data: true, reason: `Sire: ${r.sire}` }
        : { score: NEUTRAL_SCORE, has_data: false, reason: NO_DATA_LABEL },
      jockey_form: { score: r.jockey ? 5.8 : NEUTRAL_SCORE, has_data: Boolean(r.jockey), reason: r.jockey ? `Jockey: ${r.jockey}` : NO_DATA_LABEL },
      trainer_form: { score: r.trainer ? 5.6 : NEUTRAL_SCORE, has_data: Boolean(r.trainer), reason: r.trainer ? `Trainer: ${r.trainer}` : NO_DATA_LABEL },
      weight_claims: {
        score: clamp(5.2 + (avgWeight - carried) * 0.45 + (r.claim_kg || 0) * 0.3),
        has_data: true,
        reason: `Carries ${carried.toFixed(1)}kg (avg ${avgWeight.toFixed(1)}kg).`,
      },
      jockey_track_familiarity: { score: NEUTRAL_SCORE, has_data: false, reason: NO_DATA_LABEL },
      speedmap_pace: r.speedmap
        ? { score: onPace ? 6.9 : 5.3, has_data: true, reason: `Maps ${r.speedmap}.` }
        : { score: NEUTRAL_SCORE, has_data: false, reason: NO_DATA_LABEL },
      days_since_run:
        r.days_since_run !== undefined
          ? {
              score: r.days_since_run >= 10 && r.days_since_run <= 24 ? 7.0 : 5.5,
              has_data: true,
              reason: `${r.days_since_run} days since last run.`,
            }
          : { score: NEUTRAL_SCORE, has_data: false, reason: NO_DATA_LABEL },
      gear_changes: r.gear
        ? { score: r.gear.toLowerCase().includes('first') ? 6.8 : 5.3, has_data: true, reason: r.gear }
        : { score: NEUTRAL_SCORE, has_data: false, reason: 'No gear change listed.' },
      injury_history: r.vet_note
        ? { score: 3.5, has_data: true, reason: r.vet_note }
        : { score: NEUTRAL_SCORE, has_data: false, reason: NO_DATA_LABEL },
      stewards_comments: r.stewards_comment
        ? { score: 6.4, has_data: true, reason: r.stewards_comment }
        : { score: NEUTRAL_SCORE, has_data: false, reason: NO_DATA_LABEL },
      trainer_jockey_commentary: r.trainer_comment
        ? { score: 6.2, has_data: true, reason: r.trainer_comment }
        : { score: NEUTRAL_SCORE, has_data: false, reason: NO_DATA_LABEL },
      target_race_prep: { score: 5.5, has_data: true, reason: 'Standard target prep.' },
      youtube_transcripts: { score: NEUTRAL_SCORE, has_data: false, reason: NO_DATA_LABEL },
      market_fluc: {
        score: clamp(5.5 + fluc.fluc_pct / 8),
        has_data: open > 1 && live > 1,
        reason: `${fluc.direction} (${fluc.fluc_pct >= 0 ? '+' : ''}${fluc.fluc_pct}%): $${open.toFixed(2)} -> $${live.toFixed(2)}.`,
      },
      barrier_geometry: {
        score: barPct <= 0.35 ? 7.0 : barPct <= 0.7 ? 5.6 : 4.2,
        has_data: r.barrier > 0,
        reason: `Barrier ${r.barrier}/${fieldSize}.`,
      },
      class_track_distance: { score: formScore, has_data: Boolean(r.last_five), reason: `Form index ${formScore.toFixed(1)}/10.` },
    };

    const audit_23pt: AuditFactor[] = FACTOR_DEFINITIONS.map((f) => {
      const m = map[f.key];
      const weighted_points = Math.round(((m.score / 10) * f.weight) * 1000) / 1000;
      return { ...f, ...m, weighted_points };
    });

    const composite = Math.round(audit_23pt.reduce((acc, a) => acc + a.weighted_points, 0) * 100) / 100;

    return {
      rank: 0,
      runner_number: r.runner_number,
      name: r.name,
      barrier: r.barrier,
      jockey: r.jockey || 'TBA',
      trainer: r.trainer || 'Unknown',
      apprentice_claim_kg: Number(r.claim_kg) || 0,
      weight_allocated: Number(r.weight) || 57,
      weight_carried: carried,
      gear: r.gear || '',
      sire: r.sire || '',
      dam: '',
      last_twenty_starts: r.last_five || '',
      opening_win: open,
      live_win: live,
      rated_price: 0,
      is_value_overlay: false,
      edge_pct: 0,
      speedmap_label: r.speedmap || 'Midfield',
      composite_score: composite,
      market_fluc: fluc,
      probabilities: { win: 0, top2: 0, top3: 0, top4: 0, top10: 1 },
      audit_23pt,
    };
  });

  // Softmax + market blend
  const mean = scored.reduce((a, r) => a + r.composite_score, 0) / fieldSize;
  const std = Math.max(4.0, Math.sqrt(scored.reduce((a, r) => a + (r.composite_score - mean) ** 2, 0) / fieldSize));
  const exps = scored.map((r) => Math.exp((r.composite_score - mean) / (std * 0.95)));
  const expSum = exps.reduce((a, b) => a + b, 0) || 1;
  const rawMkt = scored.map((r) => (r.live_win > 1 ? 1 / r.live_win : 0));
  const mktSum = rawMkt.reduce((a, b) => a + b, 0);

  const blended = scored.map((_, i) => {
    const mp = exps[i] / expSum;
    const kp = mktSum > 0.5 ? rawMkt[i] / mktSum : mp;
    return 0.72 * mp + 0.28 * kp;
  });
  const bSum = blended.reduce((a, b) => a + b, 0) || 1;
  const winProbs = blended.map((p) => p / bSum);

  // Harville Top 2 / 3 / 4 / 10
  scored.forEach((r, i) => {
    const pi = winProbs[i];
    const p2nd = winProbs.reduce((acc, pj, j) => (j === i ? acc : acc + pj * (pi / Math.max(1 - pj, 1e-6))), 0);
    const top2 = Math.min(0.995, pi + p2nd);
    let p3rd = 0;
    winProbs.forEach((pj, j) => {
      if (j === i) return;
      winProbs.forEach((pk, k) => {
        if (k === i || k === j) return;
        p3rd += pj * (pk / Math.max(1 - pj, 1e-6)) * (pi / Math.max(1 - pj - pk, 1e-6));
      });
    });
    const top3 = fieldSize <= 3 ? 1 : Math.min(0.995, Math.max(top2, top2 + p3rd));
    const ratio = Math.max(0.35, Math.min(0.96, (1 - top3) / Math.max(1 - top2, 1e-6)));
    const top4 = fieldSize <= 4 ? 1 : Math.min(0.995, Math.max(top3, 1 - (1 - top3) * ratio));
    const top10 = fieldSize <= 10 ? 1 : Math.min(0.999, Math.max(top4, 1 - (1 - top4) * ((fieldSize - 10) / Math.max(fieldSize - 4, 1))));

    r.probabilities = {
      win: Math.round(pi * 10000) / 10000,
      top2: Math.round(top2 * 10000) / 10000,
      top3: Math.round(top3 * 10000) / 10000,
      top4: Math.round(top4 * 10000) / 10000,
      top10: Math.round(top10 * 10000) / 10000,
    };
    r.rated_price = Math.round(Math.min(151, Math.max(1.05, 1 / Math.max(pi, 1e-4))) * 100) / 100;
    r.is_value_overlay = r.live_win > 1 && r.live_win > r.rated_price;
    r.edge_pct = r.live_win > 1 ? Math.round(((r.live_win / r.rated_price) - 1) * 1000) / 10 : 0;
  });

  scored.sort((a, b) => b.probabilities.win - a.probabilities.win);
  scored.forEach((r, idx) => (r.rank = idx + 1));

  const top1 = scored[0];
  const top_10: Top10Entry[] = scored.slice(0, 10).map((r) => ({
    rank: r.rank,
    runner_number: r.runner_number,
    name: r.name,
    barrier: r.barrier,
    jockey: r.jockey,
    composite_score: r.composite_score,
    rated_price: r.rated_price,
    live_price: r.live_win,
    is_value_overlay: r.is_value_overlay,
    fluc_direction: r.market_fluc.direction,
    fluc_pct: r.market_fluc.fluc_pct,
    probabilities: r.probabilities,
  }));

  const srm_combos: SrmCombo[] = [];
  if (scored.length >= 2) {
    const raw = 1 / Math.max(0.01, scored[0].probabilities.top2 * scored[1].probabilities.top3);
    srm_combos.push({
      leg_count: 2,
      label: '2-Leg SRM',
      legs: [
        { runner_number: scored[0].runner_number, name: scored[0].name, market: 'Top 2', prob: scored[0].probabilities.top2 },
        { runner_number: scored[1].runner_number, name: scored[1].name, market: 'Top 3', prob: scored[1].probabilities.top3 },
      ],
      raw_harville_odds: Math.round(raw * 100) / 100,
      haircut_factor: SRM_CORRELATION_HAIRCUT,
      estimated_odds: Math.round(Math.max(1.25, raw * SRM_CORRELATION_HAIRCUT) * 100) / 100,
      unit_stake: 10,
    });
  }
  if (scored.length >= 3) {
    const raw =
      1 / Math.max(0.005, scored[0].probabilities.top2 * scored[1].probabilities.top3 * scored[2].probabilities.top4);
    srm_combos.push({
      leg_count: 3,
      label: '3-Leg SRM',
      legs: [
        { runner_number: scored[0].runner_number, name: scored[0].name, market: 'Top 2', prob: scored[0].probabilities.top2 },
        { runner_number: scored[1].runner_number, name: scored[1].name, market: 'Top 3', prob: scored[1].probabilities.top3 },
        { runner_number: scored[2].runner_number, name: scored[2].name, market: 'Top 4', prob: scored[2].probabilities.top4 },
      ],
      raw_harville_odds: Math.round(raw * 100) / 100,
      haircut_factor: SRM_CORRELATION_HAIRCUT,
      estimated_odds: Math.round(Math.max(1.5, raw * SRM_CORRELATION_HAIRCUT) * 100) / 100,
      unit_stake: 10,
    });
  }
  if (scored.length >= 4) {
    const raw =
      1 /
      Math.max(
        0.002,
        scored[0].probabilities.top2 *
          scored[1].probabilities.top3 *
          scored[2].probabilities.top4 *
          scored[3].probabilities.top4
      );
    srm_combos.push({
      leg_count: 4,
      label: '4-Leg SRM',
      legs: [
        { runner_number: scored[0].runner_number, name: scored[0].name, market: 'Top 2', prob: scored[0].probabilities.top2 },
        { runner_number: scored[1].runner_number, name: scored[1].name, market: 'Top 3', prob: scored[1].probabilities.top3 },
        { runner_number: scored[2].runner_number, name: scored[2].name, market: 'Top 4', prob: scored[2].probabilities.top4 },
        { runner_number: scored[3].runner_number, name: scored[3].name, market: 'Top 4', prob: scored[3].probabilities.top4 },
      ],
      raw_harville_odds: Math.round(raw * 100) / 100,
      haircut_factor: SRM_CORRELATION_HAIRCUT,
      estimated_odds: Math.round(Math.max(2.0, raw * SRM_CORRELATION_HAIRCUT) * 100) / 100,
      unit_stake: 10,
    });
  }

  const nums = scored.map((r) => r.runner_number);
  const slug = input.meeting_name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  const todayStr = input.date || new Date().toISOString().slice(0, 10);

  return {
    race_id: `manual-${slug}-r${input.race_number}-${Date.now()}`,
    date: todayStr,
    meeting_slug: slug,
    meeting_name: input.meeting_name,
    state: input.state || 'NSW',
    tier: input.tier || 'Metro',
    race_number: input.race_number,
    name: input.race_name,
    status: 'Open',
    advertised_start: new Date(Date.now() + 25 * 60000).toISOString(),
    distance: input.distance,
    distance_band: distBand,
    track_condition: cond,
    track_condition_band: condBand,
    rail_position: input.rail_position || 'True',
    weather: {
      source: 'Manual Input',
      temperature_c: 21,
      rain_chance_pct: 10,
      rain_expected_mm: 0,
      wind_speed_kmh: input.wind_speed_kmh || 8,
      wind_from_deg: 180,
      wind_compass: 'S',
      home_straight_bearing: 0,
      headwind_kmh: 0,
      crosswind_kmh: 0,
      effect: input.wind_effect || 'Light Breeze',
      pace_bias: 'Neutral',
      summary: `${cond}, ${input.wind_speed_kmh || 8} km/h wind`,
    },
    meeting_bias: {
      sample_size: 0,
      pace_bias: 'Neutral',
      barrier_bias: 'Neutral',
      summary: 'Manual race upload — scored locally in-browser.',
    },
    scored_runners: scored,
    win_selection: top1
      ? {
          runner_number: top1.runner_number,
          name: top1.name,
          barrier: top1.barrier,
          jockey: top1.jockey,
          trainer: top1.trainer,
          composite_score: top1.composite_score,
          win_prob_pct: Math.round(top1.probabilities.win * 1000) / 10,
          rated_price: top1.rated_price,
          opening_price: top1.opening_win,
          live_price: top1.live_win,
          is_value_overlay: top1.is_value_overlay,
          edge_pct: top1.edge_pct,
          market_fluc: top1.market_fluc,
        }
      : null,
    top_10,
    srm_combos,
    exotics: {
      exacta:
        nums.length >= 3
          ? {
              type: 'Exacta',
              legs: [nums.slice(0, 2), nums.slice(0, 3)],
              combinations: 4,
              flexi_pct: 250,
              unit_stake: 10,
              display: `${nums.slice(0, 2).join(',')} / ${nums.slice(0, 3).join(',')}`,
            }
          : null,
      trifecta:
        nums.length >= 4
          ? {
              type: 'Trifecta',
              legs: [nums.slice(0, 2), nums.slice(0, 4), nums.slice(0, Math.min(5, nums.length))],
              combinations: 18,
              flexi_pct: 55.56,
              unit_stake: 10,
              display: `${nums.slice(0, 2).join(',')} / ${nums.slice(0, 4).join(',')} / ${nums.slice(0, Math.min(5, nums.length)).join(',')}`,
            }
          : null,
      first4:
        nums.length >= 5
          ? {
              type: 'First 4',
              legs: [nums.slice(0, 2), nums.slice(0, 4), nums.slice(0, 5), nums.slice(0, Math.min(6, nums.length))],
              combinations: 36,
              flexi_pct: 27.78,
              unit_stake: 10,
              display: `${nums.slice(0, 2).join(',')} / ${nums.slice(0, 4).join(',')} / ${nums.slice(0, 5).join(',')} / ${nums.slice(0, Math.min(6, nums.length)).join(',')}`,
            }
          : null,
    },
    pre_jump_analyzed: true,
    is_manual_upload: true,
  };
}
