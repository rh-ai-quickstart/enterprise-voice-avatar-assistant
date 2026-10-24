"""LiveKit Agents worker.

Pipeline per user turn: Silero VAD detects the end of speech, Whisper transcribes,
the RAG API answers (grounded, with memory and guardrails, in voice mode), the
answer is published to the room for the UI and spoken through TTS. With an avatar
provider configured, the provider publishes the audio and a lip-synced video track.

Run:  python agent.py start        (worker registers with LiveKit and joins new rooms)
      python agent.py download-files
"""

import asyncio
import logging
import time

import httpx
import openai as openai_sdk
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    FlushSentinel,
    JobContext,
    JobProcess,
    RoomOutputOptions,
    StopResponse,
    WorkerOptions,
    cli,
    stt,
)
from livekit.plugins import openai, silero

from app import avatars, faces, helpers, rag_client
from app.config import settings
from app.tls import async_http_client

log = logging.getLogger("voice-agent")
# Streaming is switched off for the life of the worker when the RAG API has no streaming endpoint
_STREAM = {"enabled": True}


def streaming_enabled() -> bool:
    return _STREAM["enabled"] and settings.rag_stream and settings.guardrails_provider.lower() == "none"


def build_stt():
    client = openai_sdk.AsyncClient(
        base_url=settings.stt_base_url,
        api_key=settings.stt_api_key or "none",
        http_client=async_http_client(60),
    )
    return openai.STT(model=settings.stt_model, language=settings.stt_language, client=client)


def build_tts(face: faces.Face | None = None):
    """TTS for this session; the voice follows the chosen avatar face (see app/faces.py)."""
    client = openai_sdk.AsyncClient(
        base_url=settings.tts_base_url,
        api_key=settings.tts_api_key or "none",
        http_client=async_http_client(60),
    )
    return openai.TTS(
        model=settings.tts_model,
        voice=faces.voice_for(face),
        speed=settings.tts_speed,
        response_format=settings.tts_response_format,
        client=client,
    )


def turn_detector_enabled() -> bool:
    return settings.turn_detector.lower() not in ("", "none", "off", "false")


def build_turn_detector():
    """LiveKit's end-of-turn model, local (the v1-mini weights ship with livekit-local-inference)."""
    from livekit.agents.inference import TurnDetector

    return TurnDetector(version=settings.turn_detector)


def build_llm():
    """Direct LLM, only used by the framework if a turn bypasses llm_node."""
    return openai.LLM(
        model=settings.llm_model, base_url=settings.llm_base_url, api_key=settings.llm_api_key or "none"
    )


class Assistant(Agent):
    def __init__(
        self, room: rtc.Room, session_id: str, user_id: str | None, user_name: str | None = None
    ) -> None:
        super().__init__(instructions=settings.instructions)
        self._room = room
        self._session_id = session_id
        self._user_id = user_id
        self._user_name = user_name

    async def stt_node(self, audio, model_settings):
        """Noise never becomes words: Whisper turns short sounds and silence into "-", "Oh." or
        stock phrases. Dropped here, they neither start a turn nor interrupt the assistant (with
        an avatar only words interrupt, see entrypoint)."""
        async for event in Agent.default.stt_node(self, audio, model_settings):
            if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                text = event.alternatives[0].text
                if helpers.is_noise_transcript(text):
                    log.info("session=%s ignoring noise transcript %r", self._session_id, text[:80])
                    continue
            yield event

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        """Noise is not a question (the same check as stt_node, for a turn put together from
        several transcripts): it would get an answer, a chat bubble and a knowledge gap each."""
        text = getattr(new_message, "text_content", None) or ""
        if helpers.is_noise_transcript(text):
            log.info("session=%s ignoring noise transcript %r", self._session_id, text[:80])
            raise StopResponse()

    async def llm_node(self, chat_ctx, tools, model_settings):
        """Replace the LLM step with a call to the RAG API so voice and text share one answer path.
        With streaming, speech starts on the first complete sentence while the rest is generated."""
        text = helpers.last_user_text(chat_ctx)
        # Preemptive generation runs this before on_user_turn_completed can stop a noise turn, and
        # LiveKit does not cancel it then: noise must not reach the RAG API (an answer, a chat
        # bubble and a stored turn each)
        if not text or helpers.is_noise_transcript(text):
            return
        started = time.monotonic()
        if streaming_enabled():
            spoken = {"any": False}
            try:
                async for piece in self._stream(text, started, spoken):
                    yield piece
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (404, 405):
                    log.warning(
                        "RAG API has no streaming endpoint (%s); whole answers from now on",
                        exc.response.status_code,
                    )
                    _STREAM["enabled"] = False
                else:
                    log.warning(
                        "streamed RAG API call answered %s for session %s; retrying with a whole answer",
                        exc.response.status_code,
                        self._session_id,
                    )
            except Exception:
                log.exception("streamed RAG API call failed for session %s", self._session_id)
                if spoken["any"]:
                    return
            # a transient failure (the stream broke before anything was spoken, or an error
            # status) gets one more try with a whole answer before the apology
        try:
            reply = await rag_client.chat(text, self._session_id, self._user_id, self._user_name)
        except Exception:
            log.exception("RAG API call failed for session %s", self._session_id)
            yield "Sorry, I could not reach the knowledge base just now. Please try again in a moment."
            return
        await self._publish(reply, text)
        answer = helpers.speakable(reply.get("answer", ""))
        log.info(
            "session=%s blocked=%s answer=%r (%.2fs)",
            self._session_id,
            reply.get("blocked"),
            answer[:80],
            time.monotonic() - started,
        )
        yield answer

    async def _stream(self, text: str, started: float, spoken: dict):
        buffer = helpers.SentenceBuffer()
        reply = None
        first: float | None = None
        async for kind, payload in rag_client.chat_stream(
            text, self._session_id, self._user_id, self._user_name
        ):
            if kind == "delta":
                if first is None:
                    first = time.monotonic()
                    log.info("session=%s first token after %.2fs", self._session_id, first - started)
                for sentence in buffer.feed(payload):
                    spoken["any"] = True
                    yield sentence
                    # each sentence goes to TTS on its own: the framework's sentence splitter would
                    # otherwise hold it until the next one starts, which here means fully generated
                    yield FlushSentinel()
            else:
                reply = payload
        tail = buffer.flush()
        if tail:
            spoken["any"] = True
            yield tail
            yield FlushSentinel()
        if reply is None:
            raise RuntimeError("the RAG API stream ended without a final message")
        if not spoken["any"]:  # a service request or a blocked message arrives as the final only
            yield helpers.speakable(reply.get("answer", ""))
        await self._publish(reply, text)
        log.info(
            "session=%s blocked=%s answer=%r (streamed, %.2fs)",
            self._session_id,
            reply.get("blocked"),
            str(reply.get("answer", ""))[:80],
            time.monotonic() - started,
        )

    async def _publish(self, reply: dict, question: str) -> None:
        try:
            await self._room.local_participant.publish_data(
                helpers.citations_payload(reply, question), reliable=True, topic="assistant"
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("could not publish citations to the room: %s", exc)


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(
        min_silence_duration=settings.vad_min_silence_seconds,
        min_speech_duration=settings.vad_min_speech_seconds,
    )
    proc.userdata["turn_detector"] = False
    if turn_detector_enabled():
        try:  # load the model (~108 MB) here rather than on the first turn
            from livekit.local_inference import init_eot

            init_eot()
            proc.userdata["turn_detector"] = True
        except Exception:
            log.exception("turn detector %s unavailable; turns end on silence alone", settings.turn_detector)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    participant = await ctx.wait_for_participant()
    session_id = helpers.session_id_from_room(ctx.room.name)
    user_id = helpers.user_id_from_identity(participant.identity if participant else None)
    user_name = helpers.display_name(participant)
    face = faces.select(faces.requested_face(participant))
    log.info(
        "joined room %s (session %s) for participant %s; face=%s voice=%s",
        ctx.room.name,
        session_id,
        user_id,
        face.id if face else "configured default",
        faces.voice_for(face),
    )

    turn_handling = {
        "endpointing": {"min_delay": settings.min_endpointing_delay},
        "preemptive_generation": {"enabled": settings.preemptive_generation},
    }
    if ctx.proc.userdata.get("turn_detector"):
        turn_handling["turn_detection"] = build_turn_detector()
    if avatars.active():
        # An avatar's stream cannot pause, so LiveKit cannot hold the answer at a sound and resume
        # it when no words follow (resume_false_interruption): any noise would cut the answer off.
        # Words interrupt instead, and noise never becomes words (Assistant.stt_node).
        turn_handling["interruption"] = {
            "min_words": settings.avatar_interruption_min_words,
            "resume_false_interruption": False,
        }
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=build_stt(),
        tts=build_tts(face),
        llm=build_llm(),
        turn_handling=turn_handling,
    )
    try:
        avatar = await asyncio.wait_for(
            avatars.start(session, ctx.room, face_id=face.id if face else None),
            timeout=settings.avatar_start_timeout_seconds,
        )
    except TimeoutError:
        log.error(
            "avatar provider %s did not start within %.0fs; continuing audio-only",
            avatars.provider(),
            settings.avatar_start_timeout_seconds,
        )
        avatar = None
    except Exception:
        log.exception("avatar provider %s failed to start; continuing audio-only", avatars.provider())
        avatar = None

    async def stop_avatar() -> None:
        await avatars.stop(avatar)

    ctx.add_shutdown_callback(stop_avatar)

    human_identity = participant.identity if participant else None

    @ctx.room.on("participant_disconnected")
    def _on_participant_disconnected(who) -> None:
        # Close the room when the person leaves: the job shuts down (ending the avatar session) and
        # the next "Start voice" for the same session gets a fresh room and a fresh agent.
        if human_identity and who.identity == human_identity:
            log.info("participant %s left; closing room %s", who.identity, ctx.room.name)
            ctx.delete_room()

    await session.start(
        agent=Assistant(ctx.room, session_id, user_id, user_name),
        room=ctx.room,
        room_output_options=RoomOutputOptions(audio_enabled=avatar is None),
    )
    # Turn latency in the log: from the end of the person's speech to the first spoken audio
    turn = {"end_of_speech": None}

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        if getattr(ev, "old_state", None) == "speaking" and getattr(ev, "new_state", None) == "listening":
            turn["end_of_speech"] = time.monotonic()

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        if getattr(ev, "new_state", None) == "speaking" and turn["end_of_speech"]:
            log.info(
                "latency: end of speech to first audio %.2fs (session %s, streaming=%s)",
                time.monotonic() - turn["end_of_speech"],
                session_id,
                streaming_enabled(),
            )
            turn["end_of_speech"] = None

    # The framework's own per-turn timings: end of turn and transcript for the person's turn;
    # first token, first sentence to TTS, first audio, avatar playback and end to end for the reply
    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = getattr(ev, "item", None)
        timings = helpers.turn_timings(getattr(item, "role", None), getattr(item, "metrics", None))
        if timings:
            log.info("timings (session %s): %s", session_id, timings)

    greeting = helpers.greeting_for(user_name, settings.greeting, settings.greeting_named)
    if greeting:
        try:
            # the greeting also goes to the chat transcript, like every spoken answer
            await ctx.room.local_participant.publish_data(
                helpers.citations_payload({"session_id": session_id, "answer": greeting}, kind="greeting"),
                reliable=True,
                topic="assistant",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("could not publish the greeting to the room: %s", exc)
        await session.say(greeting, allow_interruptions=True)

    notices = asyncio.create_task(notification_loop(session, ctx.room, session_id))

    async def stop_notices() -> None:
        notices.cancel()

    ctx.add_shutdown_callback(stop_notices)


async def notification_loop(session: AgentSession, room: rtc.Room, session_id: str) -> None:
    """Speak outcome notices (ticket decisions made in Slack) as they arrive for this session."""
    while True:
        await asyncio.sleep(settings.notification_poll_seconds)
        try:
            pending = await rag_client.pending_notifications(session_id)
        except Exception as exc:  # noqa: BLE001 - polling must survive transient API errors
            log.debug("notification poll failed for session %s: %s", session_id, exc)
            continue
        for notice in pending:
            text = str(notice.get("text") or "")
            if not text:
                continue
            log.info("session=%s speaking notice for %s", session_id, notice.get("ticket_ref"))
            try:
                await rag_client.ack_notifications(session_id, [int(notice["id"])])
                await room.local_participant.publish_data(
                    helpers.citations_payload({"session_id": session_id, "answer": text}, kind="notice"),
                    reliable=True,
                    topic="assistant",
                )
                await session.say(helpers.speakable(text), allow_interruptions=True)
            except Exception:
                log.exception("could not deliver notice %s for session %s", notice.get("id"), session_id)


if __name__ == "__main__":
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            ws_url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
            port=settings.agent_port,
        )
    )
