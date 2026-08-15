import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(globalThis, "ResizeObserver", {
  value: ResizeObserverStub,
  writable: true,
});

class RequestStub {
  readonly url: string;
  readonly method: string;
  readonly signal?: AbortSignal | null;
  readonly headers: Headers;

  constructor(input: RequestInfo | URL, init: RequestInit = {}) {
    this.url = typeof input === "string" || input instanceof URL ? String(input) : input.url;
    this.method = init.method ?? "GET";
    this.signal = init.signal;
    this.headers = new Headers(init.headers);
  }
}

Object.defineProperty(globalThis, "Request", {
  value: RequestStub,
  writable: true,
});

afterEach(() => cleanup());
