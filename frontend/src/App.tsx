import { useCallback, useEffect, useState } from "react";
import { ChatPanel } from "./components/ChatPanel";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { CitationsPanel } from "./components/CitationsPanel";
import { Header } from "./components/Header";
import { StatusStrip } from "./components/StatusStrip";
import { VoicePanel } from "./components/VoicePanel";
import * as api from "./lib/api";
import type { AssistantTurn, ChatMessage, Citation, Info } from "./types";

const SESSION_KEY = "assistant.session";
const NAME_KEY = "assistant.user";

function newId() {
  return crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : Math.random().toString(36).slice(2);
}

export default function App() {
  const [sessionId, setSessionId] = useState<string>(() => sessionStorage.getItem(SESSION_KEY) ?? newId());
  const [userName, setUserName] = useState<string>(() => localStorage.getItem(NAME_KEY) ?? "");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [info, setInfo] = useState<Info | null>(null);
  const [busy, setBusy] = useState(false);
  const [voiceActive, setVoiceActive] = useState(false);

  useEffect(() => {
    sessionStorage.setItem(SESSION_KEY, sessionId);
  }, [sessionId]);
  useEffect(() => {
    localStorage.setItem(NAME_KEY, userName);
  }, [userName]);
  useEffect(() => {
    api.info().then(setInfo).catch(() => setInfo(null));
  }, []);

  // Outcome notices (a request decided in Slack) reach the session through the RAG API. During a
  // voice session the agent speaks and relays them; otherwise the page polls and shows them.
  useEffect(() => {
    if (voiceActive) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const pending = await api.notifications(sessionId);
        if (cancelled || pending.length === 0) return;
        setMessages((m) => [
          ...m,
          ...pending.map((n) => ({ id: `notice-${n.id}`, role: "assistant" as const, content: n.text, notice: true })),
        ]);
        await api.ackNotifications(sessionId, pending.map((n) => n.id));
      } catch {
        /* the API may be briefly unavailable; try again on the next tick */
      }
    };
    const timer = window.setInterval(poll, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [sessionId, voiceActive]);

  const send = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || busy) return;
      const userMessage: ChatMessage = { id: newId(), role: "user", content: question };
      const pendingId = newId();
      setMessages((m) => [...m, userMessage, { id: pendingId, role: "assistant", content: "", pending: true }]);
      setBusy(true);
      try {
        const reply = await api.chat({
          message: question,
          session_id: sessionId,
          user_id: userName.trim() || undefined,
          user_name: userName.trim() || undefined,
        });
        setMessages((m) =>
          m.map((msg) =>
            msg.id === pendingId
              ? {
                  id: pendingId,
                  role: "assistant",
                  content: reply.answer,
                  citations: reply.citations,
                  blocked: reply.blocked,
                  ticket: reply.ticket ?? null,
                }
              : msg,
          ),
        );
        setCitations(reply.citations);
        setSelected(reply.citations.find((c) => c.used)?.n ?? null);
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        setMessages((m) =>
          m.map((msg) =>
            msg.id === pendingId
              ? { id: pendingId, role: "assistant", content: `The assistant could not answer: ${detail}`, error: true }
              : msg,
          ),
        );
      } finally {
        setBusy(false);
      }
    },
    [busy, sessionId, userName],
  );

  // A new conversation leaves the old one stored: the admin portal lists it until an admin deletes it
  const reset = useCallback(async () => {
    setMessages([]);
    setCitations([]);
    setSelected(null);
    setSessionId(newId());
  }, []);

  const archive = useCallback(async () => {
    try {
      const result = await api.archiveSession(sessionId);
      const doc = result.doc_url ? ` Google Doc: ${result.doc_url}` : "";
      const text = result.requested
        ? `Archiving this conversation: the transcript is being indexed, so later questions can cite it.${doc}`
        : `The archival workflow could not be reached; check that n8n is running.${doc}`;
      setMessages((m) => [...m, { id: newId(), role: "assistant", content: text, notice: true }]);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setMessages((m) => [...m, { id: newId(), role: "assistant", content: `Archival failed: ${detail}`, error: true }]);
    }
  }, [sessionId]);

  const onAssistantTurn = useCallback((turn: AssistantTurn) => {
    const entries: ChatMessage[] = [];
    if (turn.question) entries.push({ id: newId(), role: "user", content: turn.question, voice: true });
    entries.push({
      id: newId(),
      role: "assistant",
      content: turn.answer,
      citations: turn.citations,
      blocked: turn.blocked,
      voice: true,
      ticket: turn.ticket ?? null,
      notice: turn.kind === "notice" || (!turn.kind && !turn.question),
    });
    setMessages((m) => [...m, ...entries]);
    setCitations(turn.citations);
    setSelected(turn.citations.find((c) => c.used)?.n ?? null);
  }, []);

  const showCitations = (msg: ChatMessage) => {
    if (msg.citations) {
      setCitations(msg.citations);
      setSelected(msg.citations.find((c) => c.used)?.n ?? null);
    }
  };

  return (
    <ErrorBoundary>
    <div className="app">
      <Header userName={userName} onUserName={setUserName} onReset={reset} sessionId={sessionId}  onArchive={archive} canArchive={messages.length > 0 && !busy} />
      <div className="banner" role="note">
        <strong>AI-generated answers</strong> from company documents. Verify against the cited source before acting on them.
      </div>
      <main className="layout">
        <section className="avatar-column">
          <VoicePanel sessionId={sessionId} userName={userName} onAssistantTurn={onAssistantTurn} onActiveChange={setVoiceActive} />
        </section>
        <section className="chat-column">
          <ChatPanel messages={messages} busy={busy} userName={userName} onSend={send} onCite={setSelected} onSelectMessage={showCitations} />
        </section>
        <aside className="citations-column">
          <CitationsPanel citations={citations} selected={selected} onSelect={setSelected} />
        </aside>
      </main>
      <StatusStrip info={info} />
    </div>
    </ErrorBoundary>
  );
}
