import { useEffect, useState, type CSSProperties } from "react";
import {
  BarVisualizer,
  LiveKitRoom,
  RoomAudioRenderer,
  VideoTrack,
  useConnectionState,
  useDataChannel,
  useLocalParticipant,
  useParticipantTracks,
  useRemoteParticipants,
  useRoomContext,
  useVoiceAssistant,
} from "@livekit/components-react";
import { ConnectionState, LogLevel, ParticipantKind, Track, setLogLevel } from "livekit-client";
import * as api from "../lib/api";
import type { AssistantTurn, VoiceFace, VoiceFaces, VoiceProvider, VoiceToken } from "../types";

// Open the page with ?debug=1 to get verbose LiveKit client logs (ICE candidates, TURN, reconnects).
if (new URLSearchParams(window.location.search).has("debug")) setLogLevel(LogLevel.debug);

// The agent's state and the avatar's tracks. useVoiceAssistant takes only camera and microphone
// tracks, and OpenSpline publishes the avatar's without a source (LiveKit reports them as
// unknown): those of the avatar worker, which publishes on the agent's behalf, are taken too.
export function useAvatarTracks() {
  const assistant = useVoiceAssistant();
  const agent = assistant.agent;
  const worker = useRemoteParticipants().find(
    (p) => p.kind === ParticipantKind.AGENT && !!agent && p.attributes["lk.publish_on_behalf"] === agent.identity,
  );
  const unsourced = useParticipantTracks([Track.Source.Unknown], worker?.identity);
  return {
    ...assistant,
    audioTrack: assistant.audioTrack ?? unsourced.find((t) => t.publication.kind === Track.Kind.Audio),
    videoTrack: assistant.videoTrack ?? unsourced.find((t) => t.publication.kind === Track.Kind.Video),
  };
}

// The avatar frame takes the video's shape (width / height), so a square OpenSpline video fills
// it instead of sitting between black bars; the fallback holds until the video arrives.
const WIDE = 16 / 9;
export function avatarAspect(dimensions: { width: number; height: number } | undefined, fallback: number): number {
  return dimensions && dimensions.width > 0 && dimensions.height > 0 ? dimensions.width / dimensions.height : fallback;
}
const frameStyle = (aspect: number) => ({ "--avatar-aspect": aspect }) as CSSProperties;

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

// The chosen avatar, and the face chosen for each avatar, are remembered in this browser
// (assistant.face.<avatar>; before the choice of avatar there was one face, assistant.face)
const PROVIDER_KEY = "assistant.avatar";
const FACE_KEY = "assistant.face";
const MAX_FACES = 4;

function slug(name: string) {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function readStored(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function store(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

/** The avatars offered; an older RAG API sends only the default one, with its faces at the top level. */
export function offeredProviders(result: VoiceFaces): VoiceProvider[] {
  if (result.providers) return result.providers;
  if (result.provider === "none") return [];
  return [
    {
      id: result.provider,
      name: result.provider,
      self_hosted: result.provider === "openspline",
      default: result.default,
      faces: result.faces,
    },
  ];
}

/** The wanted avatar when it is offered, otherwise the default (the first). */
export function pickProvider(providers: VoiceProvider[], wanted: string): VoiceProvider | null {
  return providers.find((p) => p.id === wanted) ?? providers[0] ?? null;
}

/** The wanted face when the avatar offers it, otherwise the avatar's default face. */
export function pickFace(provider: VoiceProvider | null, wanted: string): string {
  if (!provider) return "";
  const faces = provider.faces.slice(0, MAX_FACES);
  return faces.some((f) => f.id === wanted) ? wanted : (provider.default ?? faces[0]?.id ?? "");
}

export function VoicePanel({ sessionId, userName, onAssistantTurn, onActiveChange }: Props) {
  const [connection, setConnection] = useState<VoiceToken | null>(null);
  useEffect(() => {
    onActiveChange?.(connection !== null);
  }, [connection, onActiveChange]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Avatars and their faces the person can choose from; the agent starts the chosen avatar and
  // picks the voice that matches the face when it joins the room.
  const [providers, setProviders] = useState<VoiceProvider[]>([]);
  const [providerId, setProviderId] = useState<string>(() => readStored(PROVIDER_KEY));
  const [faceId, setFaceId] = useState("");
  useEffect(() => {
    let cancelled = false;
    api
      .voiceFaces()
      .then((result) => {
        if (!cancelled) setProviders(offeredProviders(result));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
  const provider = pickProvider(providers, providerId);
  const faces = provider ? provider.faces.slice(0, MAX_FACES) : [];
  const face = faces.find((f) => f.id === faceId) ?? null;
  // A new avatar brings back the face last chosen for it, or its default face
  useEffect(() => {
    if (provider) setFaceId(pickFace(provider, readStored(`${FACE_KEY}.${provider.id}`) || readStored(FACE_KEY)));
  }, [provider]);
  useEffect(() => {
    if (provider) store(PROVIDER_KEY, provider.id);
  }, [provider]);
  useEffect(() => {
    if (provider && face) store(`${FACE_KEY}.${provider.id}`, face.id);
  }, [provider, face]);
  // OpenSpline renders a square video; the other avatars wide ones
  const defaultAspect = provider?.id === "openspline" ? 1 : WIDE;

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      const token = await api.voiceToken({
        session_id: sessionId,
        identity: `user-${slug(userName) || "guest"}`,
        name: userName.trim() || undefined,
        face_id: face?.id,
        provider: provider?.id,
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
        <div className="avatar-frame avatar-idle" style={frameStyle(defaultAspect)}>
          <button type="button" className="mic" onClick={start} disabled={starting}>
            <span aria-hidden="true">🎙</span> {starting ? "Starting…" : "Start voice conversation"}
          </button>
        </div>
        {providers.length > 1 && (
          <div className="avatar-picker" role="radiogroup" aria-label="Avatar">
            <span className="face-picker-label">Avatar</span>
            {providers.map((p) => (
              <button
                key={p.id}
                type="button"
                role="radio"
                aria-checked={p.id === provider?.id}
                className={`avatar-option${p.id === provider?.id ? " selected" : ""}`}
                onClick={() => setProviderId(p.id)}
                title={p.self_hosted ? `${p.name} runs in this cluster` : `${p.name} is a cloud service`}
              >
                <span className="avatar-option-name">{p.name}</span>
                <span className="avatar-option-hosting">{p.self_hosted ? "Self-hosted" : "Cloud"}</span>
              </button>
            ))}
          </div>
        )}
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
      <VoiceStage
        onAssistantTurn={onAssistantTurn}
        error={error}
        faceName={face ? (providers.length > 1 && provider ? `${provider.name} · ${face.name}` : face.name) : null}
        defaultAspect={defaultAspect}
      />
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
  defaultAspect,
}: {
  onAssistantTurn: (turn: AssistantTurn) => void;
  error: string | null;
  faceName: string | null;
  defaultAspect: number;
}) {
  const { state, audioTrack, videoTrack } = useAvatarTracks();
  const aspect = avatarAspect(videoTrack?.publication.dimensions, defaultAspect);
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
      <div className="avatar-frame" style={frameStyle(aspect)}>
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
