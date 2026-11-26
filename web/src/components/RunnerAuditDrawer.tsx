import type { AuditFactor, ScoredRunner } from '../lib/types';
import { FACTOR_DEFINITIONS, NEUTRAL_SCORE, NO_DATA_LABEL } from '../lib/localScorer23pt';

export function RunnerAuditDrawer({ runner }: { runner: ScoredRunner }) {
  // Always surface all 24 factors cleanly, never omitting flat/neutral factors
  const existingMap = new Map<string, AuditFactor>();
  (runner.audit_23pt || []).forEach((f) => existingMap.set(f.key, f));

  const all24: AuditFactor[] = FACTOR_DEFINITIONS.map((def) => {
    const found = existingMap.get(def.key);
    if (found) return found;
    return {
      key: def.key,
      label: def.label,
      category: def.category,
      weight: def.weight,
      score: NEUTRAL_SCORE,
      weighted_points: Math.round(((NEUTRAL_SCORE / 10) * def.weight) * 1000) / 1000,
      has_data: false,
      reason: NO_DATA_LABEL,
    };
  });

  const categories = Array.from(new Set(all24.map((f) => f.category)));
  const liveCount = all24.filter((f) => f.has_data).length;
  const neutralCount = all24.length - liveCount;

  return (
    <div className="border-t border-slate-800 bg-slate-950/90 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold text-slate-100">
            24-Point Audit Breakdown — #{runner.runner_number} {runner.name}
          </h4>
          <p className="text-xs text-slate-400">
            Composite {runner.composite_score.toFixed(1)}/100 · Rated ${runner.rated_price.toFixed(2)} vs Live $
            {runner.live_win > 0 ? runner.live_win.toFixed(2) : '—'}
            {runner.ai_note ? ` · ${runner.ai_note}` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2 text-[10px] font-bold">
          <span className="rounded-full border border-emerald-500/35 bg-emerald-500/15 px-2.5 py-0.5 text-emerald-300">
            {liveCount} LIVE
          </span>
          <span className="rounded-full border border-slate-700 bg-slate-800/80 px-2.5 py-0.5 text-slate-400">
            {neutralCount} NEUTRAL 5.0
          </span>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {categories.map((cat) => (
          <div key={cat} className="rounded-xl border border-slate-800/80 bg-slate-900/60 p-3">
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-emerald-400">
              {cat}
            </div>
            <div className="space-y-2.5">
              {all24
                .filter((f) => f.category === cat)
                .map((f) => {
                  const pct = Math.min(100, Math.max(0, (f.score / 10) * 100));
                  const barColor = !f.has_data
                    ? 'bg-slate-600'
                    : f.score >= 7.0
                    ? 'bg-emerald-500'
                    : f.score >= 5.5
                    ? 'bg-sky-500'
                    : f.score >= 4.5
                    ? 'bg-slate-400'
                    : 'bg-rose-500';
                  return (
                    <div key={f.key} className="text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-1.5">
                          <span className="truncate font-medium text-slate-200">{f.label}</span>
                          {f.has_data ? (
                            <span className="shrink-0 rounded bg-emerald-500/15 px-1.5 py-0.2 text-[9px] font-bold uppercase tracking-wide text-emerald-300 ring-1 ring-emerald-500/30">
                              LIVE
                            </span>
                          ) : (
                            <span className="shrink-0 rounded bg-slate-800 px-1.5 py-0.2 text-[9px] font-bold uppercase tracking-wide text-slate-400 ring-1 ring-slate-700">
                              NEUTRAL 5.0
                            </span>
                          )}
                        </div>
                        <span className="shrink-0 tabular-nums text-[11px] text-slate-300">
                          <strong
                            className={
                              !f.has_data ? 'text-slate-400' : f.score >= 7 ? 'text-emerald-400' : 'text-slate-100'
                            }
                          >
                            {f.score.toFixed(1)}
                          </strong>
                          /10 <span className="text-slate-500">({f.weight}% · +{f.weighted_points.toFixed(2)})</span>
                        </span>
                      </div>
                      <div className="my-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                        <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
                      </div>
                      <p className={`text-[11px] leading-snug ${f.has_data ? 'text-slate-400' : 'italic text-slate-500'}`}>
                        {f.reason}
                      </p>
                    </div>
                  );
                })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
