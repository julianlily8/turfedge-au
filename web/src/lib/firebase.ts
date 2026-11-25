import { initializeApp, getApps } from 'firebase/app';
import {
  getFirestore,
  collection,
  getDocs,
  query,
  where,
  limit,
  Firestore,
} from 'firebase/firestore';
import type { MeetingDoc, RaceDoc, SettlementDoc } from './types';

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY || '',
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN || '',
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID || '',
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET || '',
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID || '',
  appId: import.meta.env.VITE_FIREBASE_APP_ID || '',
};

export const isFirebaseConfigured = Boolean(
  firebaseConfig.apiKey && firebaseConfig.projectId
);

let dbInstance: Firestore | null = null;

export function getDb(): Firestore | null {
  if (!isFirebaseConfigured) return null;
  if (!dbInstance) {
    const app = getApps().length ? getApps()[0] : initializeApp(firebaseConfig);
    dbInstance = getFirestore(app);
  }
  return dbInstance;
}

export async function fetchMeetingsByDate(dateStr: string): Promise<MeetingDoc[]> {
  const db = getDb();
  if (!db) return [];
  const q = query(collection(db, 'meetings'), where('date', '==', dateStr));
  const snap = await getDocs(q);
  return snap.docs.map((d) => d.data() as MeetingDoc);
}

export async function fetchRacesByDate(dateStr: string): Promise<RaceDoc[]> {
  const db = getDb();
  if (!db) return [];
  const q = query(collection(db, 'races'), where('date', '==', dateStr));
  const snap = await getDocs(q);
  return snap.docs
    .map((d) => d.data() as RaceDoc)
    .sort((a, b) => a.meeting_name.localeCompare(b.meeting_name) || a.race_number - b.race_number);
}

export async function fetchAvailableDates(): Promise<string[]> {
  const db = getDb();
  if (!db) return [];
  const q = query(collection(db, 'meetings'), limit(120));
  const snap = await getDocs(q);
  const dates = new Set<string>();
  snap.docs.forEach((d) => {
    const dt = (d.data() as MeetingDoc).date;
    if (dt) dates.add(dt);
  });
  return Array.from(dates).sort((a, b) => b.localeCompare(a));
}

export async function fetchSettlements(): Promise<SettlementDoc[]> {
  const db = getDb();
  if (!db) return [];
  const q = query(collection(db, 'settlements'), limit(500));
  const snap = await getDocs(q);
  return snap.docs
    .map((d) => d.data() as SettlementDoc)
    .sort((a, b) => (b.date || '').localeCompare(a.date || '') || (a.race_number || 0) - (b.race_number || 0));
}
