export type JsonRecord = Record<string, unknown>;

export type TogetherInvite = {
  roomId: string;
  inviterId: string;
  messageTime: number;
};

export type TogetherRoom = {
  inRoom: boolean;
  roomId: string | null;
  chatRoomId: string | null;
};

export type RealtimeCredentials = {
  accId: string;
  token: string;
  addresses: string[];
};

export type PlayingState = {
  songId: string | null;
  playStatus: "PLAY" | "PAUSE" | "UNKNOWN";
  progress: number;
  serverSeq?: number;
};

export type PlaybackSnapshot = {
  songId: string | null;
  playStatus: PlayingState["playStatus"];
  progressMs: number;
  observedAtMs: number;
  serverSeq?: number;
};

export type SongDetails = {
  id: string;
  name: string;
  artist: string;
  durationMs: number;
  coverUrl?: string;
};

export type RawLyrics = {
  lrc: string | null;
  tlyric: string | null;
  romalrc: string | null;
  klyric: string | null;
  yrc: string | null;
  ytlrc: string | null;
  yromalrc: string | null;
  raw: JsonRecord;
};

export type TogetherPhase =
  | "disabled"
  | "starting"
  | "waiting_invite"
  | "joining"
  | "listening"
  | "backoff"
  | "error";

export type TogetherWorkerStatus = {
  enabled: boolean;
  phase: TogetherPhase;
  accountId: string | null;
  roomId: string | null;
  currentSong: SongDetails | null;
  playStatus: PlayingState["playStatus"];
  lastPollAt: string | null;
  lastHeartbeatAt: string | null;
  lastError: string | null;
};

export function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : {};
}

export function readString(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

export function readNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

