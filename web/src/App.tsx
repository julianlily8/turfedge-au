import { useEffect, useRef, useState } from 'react';
import { Activity, BarChart3, CalendarClock, Cloud, Database, RefreshCw } from 'lucide-react';
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
  const todayIso = getTodaySydneyIso();
  const [activeTab, setActiveTab] = useState<'upcoming' | 'profitability'>('upcoming');
  const [dates, setDates] = useState<string[]>([todayIso]);
  const [selectedDate, setSelectedDate] = useState<string>(todayIso);
  const [meetings, setMeetings] = useState<MeetingDoc[]>([]);
  const [races, setRaces] = useState<RaceDoc[]>([]);
  const [manualRaces, setManualRaces] = useState<RaceDoc[]>([]);
  const [settlements, setSettlements] = useState<SettlementDoc[]>([]);
  const [loadingRaces, setLoadingRaces] = useState<boolean>(true);
  const [loadingSettlements, setLoadingSettlements] = useState<boolean>(true);
  const [fallbackDateNotice, setFallbackDateNotice] = useState<string | null>(null);
  const [settlementError, setSettlementError] = useState<string | null>(null);
  const hasAutoSelectedRef = useRef<boolean>(false);

  // Tab 2 (Settlements) is completely decoupled from Tab 1's date/meetings queries
  // and NEVER substitutes hardcoded mock data when Firebase is configured.
  const loadSettlements = async () => {
    setLoadingSettlements(true);
    setSettlementError(null);
    if (!isFirebaseConfigured) {
      const demo = buildDemoPreviewData();
      setSettlements(demo.settlements);
      setLoadingSettlements(false);
      return;
    }
    try {
      const sList = await fetchSettlements();
      setSettlements(sList);
    } catch (err) {
      console.error('Firestore settlements read error:', err);
      setSettlementError('Could not load settlements from Firestore. Try refreshing in a moment.');
    } finally {
      setLoadingSettlements(false);
    }
  };

  // Tab 1 (Upcoming & Recent Races) loads independently and lets users browse
  // recent/yesterday meetings automatically before today's 8:27 AM AEDT ingestion runs.
  const loadRaceDay = async (targetDate: string, allowAutoFallback: boolean) => {
    setLoadingRaces(true);
    setFallbackDateNotice(null);

    if (!isFirebaseConfigured) {
      const demo = buildDemoPreviewData();
      setMeetings(demo.meetings);
      setRaces(demo.races);
      setLoadingRaces(false);
      return;
    }

    try {
      const [availDates, mList, rList] = await Promise.all([
        fetchAvailableDates(),
        fetchMeetingsByDate(targetDate),
        fetchRacesByDate(targetDate),
      ]);

      const mergedDates = Array.from(new Set([todayIso, targetDate, ...availDates])).sort((a, b) =>
        b.localeCompare(a)
      );
      setDates(mergedDates);

      // Before 8:27 AM AEDT when today's meetings aren't ingested yet, auto-display
      // the latest available meeting date from Firestore instead of an empty screen or mock data.
      if (allowAutoFallback && mList.length === 0 && rList.length === 0 && availDates.length > 0) {
        const latestAvailable = availDates[0];
        if (latestAvailable && latestAvailable !== targetDate) {
          const [prevM, prevR] = await Promise.all([
            fetchMeetingsByDate(latestAvailable),
            fetchRacesByDate(latestAvailable),
          ]);
          setSelectedDate(latestAvailable);
          setMeetings(prevM);
          setRaces(prevR);
          setFallbackDateNotice(
            `Today's (${todayIso}) card ingests at 8:27 AM AEDT — showing latest available Firestore meetings (${latestAvailable}).`
          );
          return;
        }
      }

      setMeetings(mList);
      setRaces(rList);
    } catch (err) {
      console.error('Firestore meetings/races read error:', err);
      setMeetings([]);
      setRaces([]);
    } finally {
      setLoadingRaces(false);
    }
  };

  // Load real settlements once on mount (decoupled from selectedDate)
  useEffect(() => {
    loadSettlements();
  }, []);

  // Load meetings/races whenever selectedDate changes
  useEffect(() => {
    const allowAuto = !hasAutoSelectedRef.current && selectedDate === todayIso;
    hasAutoSelectedRef.current = true;
    loadRaceDay(selectedDate, allowAuto);
  }, [selectedDate]);

  const handleRefreshAll = () => {
    loadRaceDay(selectedDate, false);
    loadSettlements();
  };

  const combinedRaces = [...races, ...manualRaces];
  const loading = loadingRaces || loadingSettlements;

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
                {isFirebaseConfigured && (
                  <span
                    className={`rounded-full px-1.5 py-0.2 text-[10px] font-bold tabular-nums ${
                      activeTab === 'profitability'
                        ? 'bg-slate-950/20 text-slate-950'
                        : 'bg-slate-800 text-slate-300'
                    }`}
                  >
                    {settlements.length}
                  </span>
                )}
              </button>
            </div>

            <button
              type="button"
              onClick={handleRefreshAll}
              title="Refresh from Cloud Firestore"
              className="rounded-xl border border-slate-800 bg-slate-900 p-2 text-slate-300 hover:border-slate-700 hover:text-white"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin text-emerald-400' : ''}`} />
            </button>
          </div>
        </div>
      </header>

      {/* Status Banners (Only offline unconfigured preview OR helpful Tab 1 date notice) */}
      {!isFirebaseConfigured && (
        <div className="border-b border-amber-500/20 bg-amber-500/10 px-4 py-2 text-center text-xs text-amber-200">
          <Cloud className="mr-1.5 inline h-3.5 w-3.5" />
          Local Preview Mode — Firebase web env variables are not configured.
        </div>
      )}

      {isFirebaseConfigured && activeTab === 'upcoming' && fallbackDateNotice && (
        <div className="border-b border-sky-500/20 bg-sky-500/10 px-4 py-2 text-center text-xs text-sky-200">
          <CalendarClock className="mr-1.5 inline h-3.5 w-3.5" />
          {fallbackDateNotice}
        </div>
      )}

      {settlementError && activeTab === 'profitability' && (
        <div className="border-b border-rose-500/20 bg-rose-500/10 px-4 py-2 text-center text-xs text-rose-200">
          {settlementError}
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
          <TabProfitability settlements={settlements} loading={loadingSettlements} />
        )}
      </main>

      <footer className="border-t border-slate-900 py-5 text-center text-[11px] text-slate-500">
        <Database className="mr-1 inline h-3 w-3" />
        100% Free Cloud Stack: Firebase Hosting &amp; Firestore (Spark) · GitHub Actions Cron · Zero-Key Public AU Racing, BOM &amp; Betfair BSP Feeds
      </footer>
    </div>
  );
}
