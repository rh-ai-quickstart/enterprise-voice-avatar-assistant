import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const lk = vi.hoisted(() => ({
  assistant: {} as Record<string, unknown>,
  participants: [] as Array<Record<string, unknown>>,
  unsourced: [] as Array<Record<string, unknown>>,
  identities: [] as Array<string | undefined>,
}));

vi.mock("@livekit/components-react", () => ({
  useVoiceAssistant: () => lk.assistant,
  useRemoteParticipants: () => lk.participants,
  useParticipantTracks: (_sources: string[], identity?: string) => {
    lk.identities.push(identity);
    return identity ? lk.unsourced : [];
  },
}));

import { avatarAspect, offeredProviders, pickFace, pickProvider, useAvatarTracks } from "./VoicePanel";

const agent = { identity: "agent-AJ_1", kind: 4, attributes: {} };
const worker = { identity: "openspline-1", kind: 4, attributes: { "lk.publish_on_behalf": "agent-AJ_1" } };
const video = { publication: { kind: "video" }, source: "unknown" };
const audio = { publication: { kind: "audio" }, source: "unknown" };

describe("useAvatarTracks", () => {
  beforeEach(() => {
    lk.identities = [];
  });

  it("takes the avatar worker's tracks without a source (OpenSpline)", () => {
    lk.assistant = { agent, state: "listening", audioTrack: undefined, videoTrack: undefined };
    lk.participants = [agent, worker];
    lk.unsourced = [audio, video];
    const { result } = renderHook(() => useAvatarTracks());
    expect(result.current.videoTrack).toBe(video);
    expect(result.current.audioTrack).toBe(audio);
    expect(lk.identities).toContain("openspline-1");
  });

  it("keeps the camera and microphone tracks useVoiceAssistant found (Tavus)", () => {
    const camera = { publication: { kind: "video" }, source: "camera" };
    lk.assistant = { agent, state: "speaking", audioTrack: undefined, videoTrack: camera };
    lk.participants = [agent, worker];
    lk.unsourced = [video];
    const { result } = renderHook(() => useAvatarTracks());
    expect(result.current.videoTrack).toBe(camera);
  });

  it("has no video without an avatar worker (audio only)", () => {
    lk.assistant = { agent, state: "listening", audioTrack: undefined, videoTrack: undefined };
    lk.participants = [agent];
    lk.unsourced = [video];
    const { result } = renderHook(() => useAvatarTracks());
    expect(result.current.videoTrack).toBeUndefined();
  });
});

describe("avatarAspect", () => {
  it("follows the video, and the fallback until its size is known", () => {
    expect(avatarAspect({ width: 512, height: 512 }, 16 / 9)).toBe(1);
    expect(avatarAspect({ width: 1280, height: 720 }, 1)).toBeCloseTo(16 / 9);
    expect(avatarAspect(undefined, 1)).toBe(1);
    expect(avatarAspect({ width: 0, height: 0 }, 16 / 9)).toBeCloseTo(16 / 9);
  });
});

const face = (id: string) => ({ id, name: id, gender: null, voice: "af_heart", thumbnail_url: null });
const openspline = { id: "openspline", name: "OpenSpline", self_hosted: true, default: "meera", faces: [face("meera"), face("steve")] };
const tavus = { id: "tavus", name: "Tavus", self_hosted: false, default: "r1", faces: [face("r1"), face("r2")] };

describe("choosing the avatar", () => {
  it("offers every avatar the RAG API lists, and the single one of an older RAG API", () => {
    const both = { provider: "openspline", default: "meera", faces: openspline.faces, providers: [openspline, tavus] };
    expect(offeredProviders(both).map((p) => p.id)).toEqual(["openspline", "tavus"]);
    const older = { provider: "tavus", default: "r1", faces: tavus.faces };
    expect(offeredProviders(older)).toEqual([
      { id: "tavus", name: "tavus", self_hosted: false, default: "r1", faces: tavus.faces },
    ]);
    expect(offeredProviders({ provider: "none", default: null, faces: [] })).toEqual([]);
  });

  it("keeps the remembered avatar when it is still offered, else takes the default", () => {
    expect(pickProvider([openspline, tavus], "tavus")).toBe(tavus);
    expect(pickProvider([openspline, tavus], "simli")).toBe(openspline);
    expect(pickProvider([openspline, tavus], "")).toBe(openspline);
    expect(pickProvider([], "tavus")).toBeNull();
  });

  it("keeps the remembered face when the avatar has it, else takes the avatar's default face", () => {
    expect(pickFace(tavus, "r2")).toBe("r2");
    expect(pickFace(tavus, "meera")).toBe("r1"); // a face of another avatar
    expect(pickFace({ ...tavus, default: null }, "")).toBe("r1");
    expect(pickFace(null, "r2")).toBe("");
  });
});
