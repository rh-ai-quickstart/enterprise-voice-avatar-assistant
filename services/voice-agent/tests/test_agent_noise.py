from livekit.agents import Agent, stt

import agent


def _final(text):
    return stt.SpeechEvent(
        type=stt.SpeechEventType.FINAL_TRANSCRIPT, alternatives=[stt.SpeechData(language="en", text=text)]
    )


async def test_noise_never_leaves_the_stt_node(monkeypatch):
    events = [
        stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH),
        _final("-"),
        _final("I love you, I love you."),
        _final("How many vacation days do I get?"),
        stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH),
    ]

    async def default_stt_node(_agent, _audio, _settings):
        for event in events:
            yield event

    monkeypatch.setattr(Agent.default, "stt_node", default_stt_node)
    assistant = agent.Assistant(room=None, session_id="s1", user_id=None)
    out = [event async for event in assistant.stt_node(None, None)]
    finals = [e.alternatives[0].text for e in out if e.type == stt.SpeechEventType.FINAL_TRANSCRIPT]
    assert finals == ["How many vacation days do I get?"]
    assert out[0].type == stt.SpeechEventType.START_OF_SPEECH
    assert out[-1].type == stt.SpeechEventType.END_OF_SPEECH
