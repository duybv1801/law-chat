/**
 * Incremental parser for `text/event-stream` bodies read with `fetch` (no `EventSource`, since the
 * chat endpoint is a POST). Follows the SSE spec for the parts the backend uses: `event:`,
 * multi-line `data:`, comment lines (`: ping`) and `\r\n`, `\n` or `\r` line endings.
 */

export interface SseEvent {
  event: string
  data: string
}

export class SseParser {
  private buffer = ''
  private eventName = ''
  private dataLines: string[] = []

  /** Feed a decoded chunk; returns the events completed by it. */
  push(chunk: string): SseEvent[] {
    this.buffer += chunk
    const events: SseEvent[] = []
    for (;;) {
      const match = /\r\n|\n|\r/.exec(this.buffer)
      if (!match) break
      // A trailing "\r" may be the first half of a "\r\n" split across chunks.
      if (match[0] === '\r' && match.index === this.buffer.length - 1) break
      const line = this.buffer.slice(0, match.index)
      this.buffer = this.buffer.slice(match.index + match[0].length)
      const event = this.processLine(line)
      if (event) events.push(event)
    }
    return events
  }

  /**
   * End of stream. A trailing `\r` held back by `push` ends its line; an event without its final
   * blank line is dropped (spec behaviour).
   */
  finish(): SseEvent[] {
    const events: SseEvent[] = []
    if (this.buffer.endsWith('\r')) {
      const event = this.processLine(this.buffer.slice(0, -1))
      if (event) events.push(event)
    }
    this.buffer = ''
    this.eventName = ''
    this.dataLines = []
    return events
  }

  private processLine(line: string): SseEvent | null {
    if (line === '') {
      const event =
        this.dataLines.length > 0
          ? { event: this.eventName || 'message', data: this.dataLines.join('\n') }
          : null
      this.eventName = ''
      this.dataLines = []
      return event
    }
    if (line.startsWith(':')) return null
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') this.eventName = value
    else if (field === 'data') this.dataLines.push(value)
    return null
  }
}
