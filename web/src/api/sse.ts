// Fetch-based SSE client: native EventSource cannot send an Authorization
// header, so we stream /admin/stream manually with Last-Event-ID reconnect.

export type SSEHandlers = Record<string, (data: unknown, id: number) => void>;

export class WiwiStream {
  private url: string;
  private token: string;
  private handlers: SSEHandlers;
  private lastId = 0;
  private closed = false;
  private controller: AbortController | null = null;
  onStateChange?: (connected: boolean) => void;

  constructor(url: string, token: string, handlers: SSEHandlers) {
    this.url = url;
    this.token = token;
    this.handlers = handlers;
  }

  start(): void {
    void this.loop();
  }

  close(): void {
    this.closed = true;
    this.controller?.abort();
  }

  private async loop(): Promise<void> {
    let backoffMs = 1000;
    while (!this.closed) {
      try {
        this.controller = new AbortController();
        const resp = await fetch(this.url, {
          headers: {
            Authorization: `Bearer ${this.token}`,
            Accept: "text/event-stream",
            ...(this.lastId > 0 ? { "Last-Event-ID": String(this.lastId) } : {}),
          },
          signal: this.controller.signal,
        });
        if (!resp.ok || !resp.body) {
          throw new Error(`SSE HTTP ${resp.status}`);
        }
        backoffMs = 1000;
        this.onStateChange?.(true);
        await this.pump(resp.body);
        this.onStateChange?.(false);
      } catch {
        this.onStateChange?.(false);
      }
      if (this.closed) break;
      await new Promise((r) => setTimeout(r, backoffMs));
      backoffMs = Math.min(backoffMs * 2, 10_000);
    }
  }

  private async pump(body: ReadableStream<Uint8Array>): Promise<void> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // frames are separated by a blank line
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        this.handleFrame(frame);
      }
    }
  }

  private handleFrame(frame: string): void {
    let id = this.lastId;
    let event = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("id:")) id = Number(line.slice(3).trim());
      else if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (Number.isFinite(id) && id > this.lastId) this.lastId = id;
    const handler = this.handlers[event];
    if (!handler) return;
    let payload: unknown = null;
    const raw = dataLines.join("\n");
    try {
      payload = raw ? JSON.parse(raw) : null;
    } catch {
      payload = raw;
    }
    handler(payload, id);
  }
}
