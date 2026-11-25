import React, { useState } from 'react';
import { Upload, FileJson, FileSpreadsheet, AlertCircle, CheckCircle2 } from 'lucide-react';
import { scoreManualRace, type ManualRaceInput, type ManualRunnerInput } from '../lib/localScorer23pt';
import { SAMPLE_CSV_TEMPLATE, SAMPLE_JSON_TEMPLATE } from '../lib/sampleData';
import type { RaceDoc } from '../lib/types';

interface Props {
  onRaceScored: (race: RaceDoc) => void;
}

function parseCsvLine(line: string): string[] {
  const result: string[] = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      inQuotes = !inQuotes;
    } else if (ch === ',' && !inQuotes) {
      result.push(cur.trim());
      cur = '';
    } else {
      cur += ch;
    }
  }
  result.push(cur.trim());
  return result;
}

function parseCsvToRace(csvText: string, meta: { meeting: string; raceNumber: number; distance: number; condition: string; rail: string }): ManualRaceInput {
  const lines = csvText
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length < 2) {
    throw new Error('CSV must include a header row and at least one runner row.');
  }
  const headers = parseCsvLine(lines[0]).map((h) => h.toLowerCase());
  const runners: ManualRunnerInput[] = lines.slice(1).map((line, idx) => {
    const cols = parseCsvLine(line);
    const get = (k: string) => {
      const i = headers.indexOf(k);
      return i >= 0 ? cols[i] || '' : '';
    };
    return {
      runner_number: Number(get('runner_number')) || idx + 1,
      name: get('name') || `Runner ${idx + 1}`,
      barrier: Number(get('barrier')) || idx + 1,
      weight: Number(get('weight')) || 57,
      claim_kg: Number(get('claim_kg')) || 0,
      jockey: get('jockey'),
      trainer: get('trainer'),
      opening_win: Number(get('opening_win')) || undefined,
      live_win: Number(get('live_win')) || undefined,
      speedmap: get('speedmap'),
      last_five: get('last_five'),
      gear: get('gear'),
      sire: get('sire'),
      days_since_run: get('days_since_run') ? Number(get('days_since_run')) : undefined,
      stewards_comment: get('stewards_comment') || undefined,
      trial_note: get('trial_note') || undefined,
      vet_note: get('vet_note') || undefined,
      trainer_comment: get('trainer_comment') || undefined,
    };
  });

  return {
    meeting_name: meta.meeting || 'Manual Meeting',
    tier: 'Metro',
    state: 'NSW',
    race_number: meta.raceNumber || 1,
    race_name: `Manual Race ${meta.raceNumber || 1} (${meta.distance || 1200}m)`,
    distance: meta.distance || 1200,
    track_condition: meta.condition || 'Good4',
    rail_position: meta.rail || 'True',
    runners,
  };
}

export function ManualDropzone({ onRaceScored }: Props) {
  const [rawText, setRawText] = useState('');
  const [meeting, setMeeting] = useState('Royal Randwick');
  const [raceNumber, setRaceNumber] = useState(7);
  const [distance, setDistance] = useState(1200);
  const [condition, setCondition] = useState('Soft5');
  const [rail, setRail] = useState('+4m Entire');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const processPayload = (text: string) => {
    setError(null);
    setSuccess(null);
    const trimmed = text.trim();
    if (!trimmed) {
      setError('Paste or drop a CSV or JSON race card first.');
      return;
    }
    try {
      let input: ManualRaceInput;
      if (trimmed.startsWith('{')) {
        const parsed = JSON.parse(trimmed) as ManualRaceInput;
        if (!Array.isArray(parsed.runners) || !parsed.runners.length) {
          throw new Error('JSON must contain a non-empty "runners" array.');
        }
        input = parsed;
      } else {
        input = parseCsvToRace(trimmed, { meeting, raceNumber, distance, condition, rail });
      }
      const scored = scoreManualRace(input);
      onRaceScored(scored);
      setSuccess(`Scored ${scored.meeting_name} R${scored.race_number} (${scored.scored_runners.length} runners) using the 23-point engine.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to parse CSV/JSON race data.');
    }
  };

  const handleFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      const content = String(reader.result || '');
      setRawText(content);
      processPayload(content);
    };
    reader.readAsText(file);
  };

  const downloadTemplate = (filename: string, content: string, mime: string) => {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">Manual CSV / JSON Race-Data Dropzone Fallback</h3>
          <p className="text-xs text-slate-400">
            Drop or paste any race card to score all 23 factors, Harville Top 2/3/4/10, Value Overlays, and Exotics locally in your browser.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => downloadTemplate('sample_race.csv', SAMPLE_CSV_TEMPLATE, 'text/csv')}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700"
          >
            <FileSpreadsheet className="h-3.5 w-3.5 text-emerald-400" /> Sample CSV
          </button>
          <button
            type="button"
            onClick={() => downloadTemplate('sample_race.json', SAMPLE_JSON_TEMPLATE, 'application/json')}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700"
          >
            <FileJson className="h-3.5 w-3.5 text-sky-400" /> Sample JSON
          </button>
          <button
            type="button"
            onClick={() => {
              setRawText(SAMPLE_CSV_TEMPLATE);
              processPayload(SAMPLE_CSV_TEMPLATE);
            }}
            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600/20 px-2.5 py-1.5 text-xs font-semibold text-emerald-300 ring-1 ring-emerald-500/40 hover:bg-emerald-600/30"
          >
            Load &amp; Score Sample
          </button>
        </div>
      </div>

      {/* Metadata inputs for CSV mode */}
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
        <input
          value={meeting}
          onChange={(e) => setMeeting(e.target.value)}
          placeholder="Meeting"
          className="rounded-lg border border-slate-800 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 focus:border-emerald-500 focus:outline-none"
        />
        <input
          type="number"
          value={raceNumber}
          onChange={(e) => setRaceNumber(Number(e.target.value))}
          placeholder="Race #"
          className="rounded-lg border border-slate-800 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 focus:border-emerald-500 focus:outline-none"
        />
        <input
          type="number"
          value={distance}
          onChange={(e) => setDistance(Number(e.target.value))}
          placeholder="Distance (m)"
          className="rounded-lg border border-slate-800 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 focus:border-emerald-500 focus:outline-none"
        />
        <input
          value={condition}
          onChange={(e) => setCondition(e.target.value)}
          placeholder="Track (e.g. Soft5)"
          className="rounded-lg border border-slate-800 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 focus:border-emerald-500 focus:outline-none"
        />
        <input
          value={rail}
          onChange={(e) => setRail(e.target.value)}
          placeholder="Rail (e.g. +4m)"
          className="rounded-lg border border-slate-800 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 focus:border-emerald-500 focus:outline-none"
        />
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragging(false);
          const f = e.dataTransfer.files?.[0];
          if (f) handleFile(f);
        }}
        className={`mt-3 rounded-xl border-2 border-dashed p-4 text-center transition ${
          isDragging ? 'border-emerald-400 bg-emerald-500/10' : 'border-slate-700 bg-slate-950/60'
        }`}
      >
        <Upload className="mx-auto h-6 w-6 text-slate-400" />
        <p className="mt-1 text-xs text-slate-300">
          Drag &amp; drop a <code className="text-emerald-400">.csv</code> or <code className="text-sky-400">.json</code> file here, or{' '}
          <label className="cursor-pointer font-semibold text-emerald-400 underline">
            browse
            <input
              type="file"
              accept=".csv,.json,text/csv,application/json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleFile(f);
              }}
            />
          </label>
        </p>
        <textarea
          rows={4}
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          placeholder="...or paste raw CSV / JSON race data here and click Score Race"
          className="mt-3 w-full rounded-lg border border-slate-800 bg-slate-900/90 p-2.5 font-mono text-[11px] text-slate-200 focus:border-emerald-500 focus:outline-none"
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <div className="text-left text-xs">
            {error && (
              <span className="inline-flex items-center gap-1 text-rose-400">
                <AlertCircle className="h-3.5 w-3.5" /> {error}
              </span>
            )}
            {success && (
              <span className="inline-flex items-center gap-1 text-emerald-400">
                <CheckCircle2 className="h-3.5 w-3.5" /> {success}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={() => processPayload(rawText)}
            className="rounded-lg bg-emerald-500 px-4 py-1.5 text-xs font-semibold text-slate-950 hover:bg-emerald-400"
          >
            Score 23-Point Race
          </button>
        </div>
      </div>
    </div>
  );
}
