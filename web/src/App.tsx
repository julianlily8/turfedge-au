import { useEffect, useState } from 'react';
import { Activity, BarChart3, Cloud, Database, RefreshCw } from 'lucide-react';
import {
  fetchAvailableDates,
  fetchMeetingsByDate,
  fetchRacesByDate,
  fetchSettlements,
  isFirebaseConfigured,
} from './lib/firebase';
import { buildDemoPreviewData } from './lib/sampleData';
import type { MeetingDoc, RaceDoc, SettlementDoc } from './lib/types';
import { TabUpcoming } from './components/TabUpcoming';
import { TabProfitability } from './components/TabProfitability';

function getTodaySydneyIso(): string {
  try {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Australia/Sydney',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(new Date());
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}

export default function App() {
  const [activeTab, setActiveTab] = useState<'upcoming' | 'profitability'>('upcoming');
  const [dates, setDates] = useState<string[]>([getTodaySydneyIso()]);
  const [selectedDate, setSelectedDate] = useState<string>(getTodaySydneyIso());
  const [meetings, setMeetings] = useState<MeetingDoc[]>([]);
  const [races, setRaces] = useState<RaceDoc[]>([]);
  const [manualRaces, setManualRaces] = useState<RaceDoc[]>([]);
  const [settlements, setSettlements] = useState<SettlementDoc[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [usingDemo, setUsingDemo] = useState<boolean>(!isFirebaseConfigured);

  const loadCloudData = async (targetDate: string) => {
    setLoading(true);
    if (!isFirebaseConfigured) {
      const demo = buildDemoPreviewData();
      setMeetings(demo.meetings);
      setRaces(demo.races);
      setSettlements(demo.settlements);
      setUsingDemo(true);
      setLoading(false);
      return;
    }

    try {
      const [availDates, mList, rList, sList] = await Promise.all([
        fetchAvailableDates(),
        fetchMeetingsByDate(targetDate),
        fetchRacesByDate(targetDate),
        fetchSettlements(),
      ]);

      const mergedDates = Array.from(new Set([targetDate, ...availDates])).sort((a, b) =>
        b.localeCompare(a)
      );
      setDates(mergedDates);

      if (mList.length === 0 && rList.length === 0 && sList.length === 0) {
        // Fallback to demo preview only until the first GitHub Actions run populates Firestore
        const demo = buildDemoPreviewData();
        setMeetings(demo.meetings);
        setRaces(demo.races);
        setSettlements(demo.settlements);
        setUsingDemo(true);
      } else {
        setMeetings(mList);
        setRaces(rList);
        setSettlements(sList);
        setUsingDemo(false);
      }
    } catch (err) {
      console.error('Firestore read error:', err);
      const demo = buildDemoPreviewData();
      setMeetings(demo.meetings);
      setRaces(demo.races);
      setSettlements(demo.settlements);
      setUsingDemo(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCloudData(selectedDate);
  }, [selectedDate]);

  const combinedRaces = [...races, ...manualRaces];

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Top Navigation Bar */}
      <header className="sticky top-0 z-30 border-b border-slate-800/80 bg-slate-950/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-400 to-teal-600 font-black text-slate-950 shadow-lg shadow-emerald-500/20">
              TE
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-base font-extrabold tracking-tight text-white">TurfEdge AU</h1>
                <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-bold text-emerald-300 ring-1 ring-emerald-500/30">
                  $0 SPARK CLOUD
                </span>
              </div>
              <p className="text-[11px] text-slate-400">
                23-Point Selection · Market Fluc · Gemini 3.1 Flash Lite · POT% Tracker
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div className="inline-flex rounded-xl border border-slate-800 bg-slate-900 p-1">
              <button
                type="button"
                onClick={() => setActiveTab('upcoming')}
                className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-xs font-semibold transition ${
                  activeTab === 'upcoming'
                    ? 'bg-emerald-500 text-slate-950 shadow'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <Activity className="h-3.5 w-3.5" /> Upcoming Races &amp; Bets
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('profitability')}
                className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-xs font-semibold transition ${
                  activeTab === 'profitability'
                    ? 'bg-emerald-500 text-slate-950 shadow'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <BarChart3 className="h-3.5 w-3.5" /> Results &amp; Profitability
              </button>
            </div>

            <button
              type="button"
              onClick={() => loadCloudData(selectedDate)}
              title="Refresh from Cloud Firestore"
              className="rounded-xl border border-slate-800 bg-slate-900 p-2 text-slate-300 hover:border-slate-700 hover:text-white"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin text-emerald-400' : ''}`} />
            </button>
          </div>
        </div>
      </header>

      {/* Cloud Status Banner */}
      {usingDemo && (
        <div className="border-b border-amber-500/20 bg-amber-500/10 px-4 py-2 text-center text-xs text-amber-200">
          <Cloud className="mr-1.5 inline h-3.5 w-3.5" />
          {isFirebaseConfigured
            ? 'Connected to Firestore — showing preview data until your first GitHub Actions Morning Ingestion run populates today’s meetings.'
            : 'Preview Mode — configure Firebase web env variables & deploy to stream live daily Australian meetings from Cloud Firestore.'}
        </div>
      )}

      {/* Main Content */}
      <main className="mx-auto max-w-7xl px-4 py-6">
        {activeTab === 'upcoming' ? (
          <TabUpcoming
            dates={dates}
            selectedDate={selectedDate}
            onDateChange={setSelectedDate}
            meetings={meetings}
            races={combinedRaces}
            onManualRaceAdded={(r) => setManualRaces((prev) => [r, ...prev])}
          />
        ) : (
          <TabProfitability settlements={settlements} />
        )}
      </main>

      <footer className="border-t border-slate-900 py-5 text-center text-[11px] text-slate-500">
        <Database className="mr-1 inline h-3 w-3" />
        100% Free Cloud Stack: Firebase Hosting &amp; Firestore (Spark) · GitHub Actions Cron · Zero-Key Public AU Racing, BOM &amp; Betfair BSP Feeds
      </footer>
    </div>
  );
}
