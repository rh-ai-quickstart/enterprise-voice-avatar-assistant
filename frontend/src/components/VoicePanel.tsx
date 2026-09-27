import { useEffect, useState } from "react";
import {
  BarVisualizer,
  LiveKitRoom,
  RoomAudioRenderer,
  VideoTrack,
  useConnectionState,
  useDataChannel,
  useLocalParticipant,
  useRoomContext,
  useVoiceAssistant,
} from "@livekit/components-react";
import { ConnectionState, LogLevel, setLogLevel } from "livekit-client";
import * as api from "../lib/api";
import type { AssistantTurn, VoiceFace, VoiceToken } from "../types";

// Open the page with ?debug=1 to get verbose LiveKit client logs (ICE candidates, TURN, reconnects).
if (new URLSearchParams(window.location.search).has("debug")) setLogLevel(LogLevel.debug);

interface Props {
  sessionId: string;
  userName: string;
  onAssistantTurn: (turn: AssistantTurn) => void;
  onActiveChange?: (active: boolean) => void;
}

const STATE_LABEL: Record<string, string> = {
  connecting: "Connecting…",
  initializing: "Assistant is joining…",
  listening: "Listening",
  thinking: "Thinking…",
  speaking: "Speaking",
  disconnected: "Assistant not connected",
};

const FACE_KEY = "assistant.face"; // the chosen face is remembered in this browser
const MAX_FACES = 4;

function slug(name: string) {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function readStoredFace(): string {
  try {
    return localStorage.getItem(FACE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function VoicePanel({ sessionId, userName, onAssistantTurn, onActiveChange }: Props) {
  const [connection, setConnection] = useState<VoiceToken | null>(null);
  useEffect(() => {
    onActiveChange?.(connection !== null);
  }, [connection, onActiveChange]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Faces the person can choose from; the agent picks the matching voice when it joins the room.
  const [faces, setFaces] = useState<VoiceFace[]>([]);
  const [faceId, setFaceId] = useState<string>(readStoredFace);
  useEffect(() => {
    let cancelled = false;
    api
      .voiceFaces()
      .then((result) => {
        if (cancelled) return;
        const offered = result.faces.slice(0, MAX_FACES);
        setFaces(offered);
        // keep the remembered face when it is still offered; otherwise fall back to the default
        setFaceId((current) => (offered.some((f) => f.id === current) ? current : (result.default ?? "")));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    try {
      if (faceId) localStorage.setItem(FACE_KEY, faceId);
    } catch {
      /* storage unavailable */
    }
  }, [faceId]);
  const face = faces.find((f) => f.id === faceId) ?? null;

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      const token = await api.voiceToken({
        session_id: sessionId,
        identity: `user-${slug(userName) || "guest"}`,
        name: userName.trim() || undefined,
        face_id: faceId || undefined,
      });
      setConnection(token);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  if (!connection) {
    return (
      <div className="voice-stage">
        <div className="avatar-frame avatar-idle">
          <button type="button" className="mic" onClick={start} disabled={starting}>
            <span aria-hidden="true">🎙</span> {starting ? "Starting…" : "Start voice conversation"}
          </button>
        </div>
        {faces.length > 1 && (
          <div className="face-picker" role="radiogroup" aria-label="Avatar face">
            <span className="face-picker-label">Avatar face</span>
            {faces.map((f) => (
              <button
                key={f.id}
                type="button"
                role="radio"
                aria-checked={f.id === faceId}
                className={`face-option${f.id === faceId ? " selected" : ""}`}
                onClick={() => setFaceId(f.id)}
                title={f.name}
              >
                <FaceThumb face={f} />
                <span className="face-name">{f.name}</span>
              </button>
            ))}
          </div>
        )}
        <span className="muted">
          Talk to the assistant. Voice and text share the same conversation, so it remembers what you asked here.
        </span>
        {error && <span className="voice-error">{error}</span>}
      </div>
    );
  }

  return (
    <LiveKitRoom
      token={connection.token}
      serverUrl={connection.url}
      connect
      audio
      video={false}
      onDisconnected={() => setConnection(null)}
      onError={(e) => setError(e.message)}
      className="voice-room"
    >
      <VoiceStage onAssistantTurn={onAssistantTurn} error={error} faceName={face?.name ?? null} />
      <RoomAudioRenderer />
    </LiveKitRoom>
  );
}

function FaceThumb({ face }: { face: VoiceFace }) {
  // The RAG API cuts a still from the Tavus thumbnail video; until it is ready (or when there is
  // none) the initial stands in.
  const [failed, setFailed] = useState(false);
  if (!face.poster_url || failed) {
    return (
      <span className={`face-thumb${face.gender ? ` ${face.gender}` : ""}`} aria-hidden="true">
        {face.name.trim().charAt(0).toUpperCase() || "?"}
      </span>
    );
  }
  return <img className="face-thumb" src={api.apiUrl(face.poster_url)} alt="" loading="lazy" onError={() => setFailed(true)} />;
}

function VoiceStage({
  onAssistantTurn,
  error,
  faceName,
}: {
  onAssistantTurn: (turn: AssistantTurn) => void;
  error: string | null;
  faceName: string | null;
}) {
  const { state, audioTrack, videoTrack } = useVoiceAssistant();
  const connectionState = useConnectionState();
  const { localParticipant, isMicrophoneEnabled } = useLocalParticipant();
  const room = useRoomContext();

  useDataChannel("assistant", (message) => {
    try {
      const payload = JSON.parse(new TextDecoder().decode(message.payload)) as AssistantTurn;
      if (payload.type === "assistant.answer") onAssistantTurn(payload);
    } catch {
      /* ignore malformed messages */
    }
  });

  const label =
    connectionState !== ConnectionState.Connected ? `Room: ${connectionState}` : (STATE_LABEL[state] ?? state);

  // LiveKit dispatches the agent once, when the room is created. If no worker was available at that
  // instant (for example during a rollout) the room never gets one, so tell the person what to do.
  const [stalled, setStalled] = useState(false);
  useEffect(() => {
    const waiting = connectionState === ConnectionState.Connected && (state === "connecting" || state === "initializing");
    if (!waiting) {
      setStalled(false);
      return;
    }
    const timer = window.setTimeout(() => setStalled(true), 20000);
    return () => window.clearTimeout(timer);
  }, [connectionState, state]);

  return (
    <div className="voice-stage">
      <div className="avatar-frame">
        {videoTrack ? (
          <VideoTrack trackRef={videoTrack} className="avatar-video" />
        ) : (
          <BarVisualizer state={state} trackRef={audioTrack} barCount={7} options={{ minHeight: 12 }} className="visualizer" />
        )}
      </div>
      <div className="voice-controls">
        <span className={`voice-state state-${state}`}>{label}</span>
        {faceName && <span className="voice-face">{faceName}</span>}
        <button type="button" className="secondary" onClick={() => localParticipant.setMicrophoneEnabled(!isMicrophoneEnabled)}>
          {isMicrophoneEnabled ? "Mute" : "Unmute"}
        </button>
        <button type="button" className="secondary" onClick={() => room.disconnect()}>
          End voice
        </button>
        {error && <span className="voice-error">{error}</span>}
        {stalled && !error && (
          <span className="voice-error">The assistant has not joined this room. Click End voice, then start again.</span>
        )}
      </div>
      <p className="ai-label">Spoken answers are AI-generated from company documents; the sources appear on the right.</p>
    </div>
  );
}
