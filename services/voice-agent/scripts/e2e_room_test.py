"""End-to-end check of the voice pipeline without a browser.

Joins a LiveKit room as a user, waits for the agent, speaks a question by
publishing TTS-generated audio as a microphone track, then waits for the
agent's answer on the room data channel (topic "assistant"). Meant to run
inside the cluster, for example from the voice-agent pod, which has the
LiveKit SDK and the same environment variables as the agent.

    python scripts/e2e_room_test.py "How often must administrator passwords be rotated?"
"""

import asyncio
import io
import json
import os
import sys
import time
import wave

import httpx
from livekit import api, rtc

QUESTION = sys.argv[1] if len(sys.argv) > 1 else "How often must administrator passwords be rotated?"
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "secret")
TTS_BASE_URL = os.environ.get("TTS_BASE_URL", "http://tts:8880/v1")
TTS_MODEL = os.environ.get("TTS_MODEL", "kokoro")
TTS_VOICE = os.environ.get("TTS_VOICE", "af_heart")
ROOM = f"session-e2e-{int(time.time())}"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def synthesize(text: str) -> tuple[int, int, bytes]:
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{TTS_BASE_URL.rstrip('/')}/audio/speech",
            json={"model": TTS_MODEL, "voice": TTS_VOICE, "input": text, "response_format": "wav"},
        )
        response.raise_for_status()
    with wave.open(io.BytesIO(response.content)) as w:
        return w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())


async def speak(room: rtc.Room, sample_rate: int, channels: int, pcm: bytes) -> None:
    source = rtc.AudioSource(sample_rate, channels)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    samples_per_chunk = sample_rate // 100  # 10 ms
    chunk_bytes = samples_per_chunk * channels * 2
    silence = bytes(chunk_bytes)
    chunks = [pcm[i : i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes)]
    chunks = [c if len(c) == chunk_bytes else c + bytes(chunk_bytes - len(c)) for c in chunks]
    chunks += [silence] * 200  # 2 s of silence so the VAD sees the end of the turn
    for chunk in chunks:
        await source.capture_frame(
            rtc.AudioFrame(
                data=chunk,
                sample_rate=sample_rate,
                num_channels=channels,
                samples_per_channel=samples_per_chunk,
            )
        )


async def main() -> int:
    token = (
        api.AccessToken(API_KEY, API_SECRET)
        .with_identity("user-e2e")
        .with_name("e2e test")
        .with_grants(api.VideoGrants(room_join=True, room=ROOM))
        .to_jwt()
    )
    room = rtc.Room()
    agent_joined = asyncio.Event()
    answers: asyncio.Queue = asyncio.Queue()

    @room.on("participant_connected")
    def _on_participant(p: rtc.RemoteParticipant) -> None:
        log(f"participant joined: {p.identity}")
        agent_joined.set()

    @room.on("track_subscribed")
    def _on_track(track, publication, participant) -> None:
        log(f"subscribed to {track.kind} track from {participant.identity}")

    @room.on("data_received")
    def _on_data(packet: rtc.DataPacket) -> None:
        if packet.topic == "assistant":
            answers.put_nowait(json.loads(packet.data))

    log(f"connecting to {LIVEKIT_URL} room {ROOM}")
    await room.connect(LIVEKIT_URL, token)
    if not any(p for p in room.remote_participants.values()):
        try:
            await asyncio.wait_for(agent_joined.wait(), timeout=60)
        except TimeoutError:
            log("FAIL: no agent joined the room within 60 s")
            return 1
    log("agent present; waiting 6 s for its greeting")
    await asyncio.sleep(6)

    log(f"synthesizing the question with TTS: {QUESTION!r}")
    sample_rate, channels, pcm = await synthesize(QUESTION)
    log(f"speaking {len(pcm) / (sample_rate * channels * 2):.1f} s of audio")
    await speak(room, sample_rate, channels, pcm)

    try:
        reply = await asyncio.wait_for(answers.get(), timeout=120)
    except TimeoutError:
        log("FAIL: no answer received on the data channel within 120 s")
        return 1
    log(f"answer: {reply.get('answer')!r}")
    log(
        f"blocked={reply.get('blocked')} citations={[(c.get('n'), c.get('source'), c.get('used')) for c in reply.get('citations', [])]}"
    )
    await room.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
