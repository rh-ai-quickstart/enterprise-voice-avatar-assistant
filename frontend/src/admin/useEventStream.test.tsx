import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RETRY_SECONDS, useEventStream } from "./useEventStream";

/** Stands in for the browser's EventSource; the tests drive it. */
class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readyState = FakeEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners: Record<string, ((e: { data: string }) => void)[]> = {};
  closed = false;
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: (e: { data: string }) => void) {
    (this.listeners[type] ??= []).push(listener);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.();
  }
  send(type: string, data: unknown) {
    for (const listener of this.listeners[type] ?? []) listener({ data: JSON.stringify(data) });
  }
  fail(readyState: number) {
    this.readyState = readyState;
    this.onerror?.();
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

describe("useEventStream", () => {
  it("is live once open and hands on activity messages", () => {
    const onMessage = vi.fn();
    const { result } = renderHook(() => useEventStream(onMessage));
    expect(result.current).toBe("connecting");
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe("/api/v1/admin/stream");
    act(() => source.open());
    expect(result.current).toBe("live");
    act(() => source.send("activity", { id: 5, kind: "ticket.approved", ref_type: "ticket", ref_id: "REQ-000001" }));
    expect(onMessage).toHaveBeenCalledWith({ id: 5, kind: "ticket.approved", ref_type: "ticket", ref_id: "REQ-000001" });
  });

  it("a refused stream turns to polling and is tried again later", () => {
    vi.useFakeTimers();
    const onClosed = vi.fn();
    const { result } = renderHook(() => useEventStream(vi.fn(), onClosed));
    act(() => FakeEventSource.instances[0].fail(FakeEventSource.CLOSED));
    expect(result.current).toBe("polling");
    expect(onClosed).toHaveBeenCalledOnce();
    expect(FakeEventSource.instances).toHaveLength(1);
    act(() => vi.advanceTimersByTime(RETRY_SECONDS * 1000));
    expect(FakeEventSource.instances).toHaveLength(2);
    act(() => FakeEventSource.instances[1].open());
    expect(result.current).toBe("live");
  });

  it("network errors leave reconnecting to the browser, and say polling after three", () => {
    const { result } = renderHook(() => useEventStream(vi.fn()));
    const source = FakeEventSource.instances[0];
    act(() => source.open());
    act(() => source.fail(FakeEventSource.CONNECTING));
    expect(result.current).toBe("connecting");
    act(() => source.fail(FakeEventSource.CONNECTING));
    act(() => source.fail(FakeEventSource.CONNECTING));
    expect(result.current).toBe("polling");
    expect(FakeEventSource.instances).toHaveLength(1);
    act(() => source.open());
    expect(result.current).toBe("live");
  });

  it("closes the stream when the portal unmounts", () => {
    const { unmount } = renderHook(() => useEventStream(vi.fn()));
    unmount();
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });
});
