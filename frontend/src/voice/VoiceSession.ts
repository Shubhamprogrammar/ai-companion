// Real-time voice over the backend WebSocket (backend/docs/websocket-protocol.md).
//
// Mic audio is captured with Web Audio and sent as raw pcm16; replies come back as raw
// 24 kHz pcm and are scheduled gap-free on an AudioContext. This is the same approach as
// the backend's /voice-test page, wrapped up for the React UI.

import { API_BASE, accessToken, onSessionChange } from "../api/client";
import type { ChatResponse, ToolAction, ToolExecution, UUID } from "../api/types";

export type VoiceStatus = "connecting" | "idle" | "listening" | "thinking" | "speaking" | "closed";

export type ServerEvent =
  | {
      type: "session.ready";
      session_id: string;
      conversation_id: UUID | null;
      output_sample_rate?: number;
    }
  | { type: "speech.started" }
  | { type: "speech.stopped"; bytes?: number }
  | { type: "transcript.delta"; text: string }
  | { type: "transcript.done"; text: string }
  | { type: "response.started"; response_id: string }
  | { type: "response.text.delta"; response_id: string; text: string }
  | { type: "tool.started"; tool: string }
  | { type: "tool.completed"; action: ToolAction }
  | { type: "response.audio.started"; response_id: string; sample_rate?: number }
  | { type: "response.audio.done"; response_id: string }
  | ({ type: "response.done"; response_id: string } & ChatResponse)
  | { type: "response.cancelled"; response_id: string; saved_text?: string }
  | { type: "confirmation.result"; execution: ToolExecution }
  | { type: "error"; code: string; message: string; fatal: boolean; response_id?: string }
  | { type: "auth.refreshed"; expires_at: string }
  | { type: "pong" }
  | { type: "session.closed"; reason: string };

export interface VoiceOptions {
  handsFree: boolean;
  /** RMS level that counts as speech in hands-free mode. */
  threshold: number;
  voice?: string | null;
  speed?: number;
}

export interface VoiceHandlers {
  onStatus(status: VoiceStatus): void;
  onLevel(rms: number): void;
  onEvent(event: ServerEvent): void;
  onClose(info: { code: number; reason: string; message: string }): void;
  onMicError?(message: string): void;
}

const CLOSE_MESSAGES: Record<number, string> = {
  1000: "Voice session ended.",
  1011: "The server hit an error. Please try again.",
  4400: "Protocol error.",
  4401: "Your session expired. Sign in again to keep talking.",
  4404: "That conversation no longer exists.",
  4408: "Connection timed out.",
  4429: "Too many voice sessions open. Close one in another tab and try again.",
};

const SILENCE_TO_END_MS = 900;
const PREROLL_FRAMES = 3;

function wsUrl(conversationRef: string): string {
  const base = new URL(API_BASE, window.location.href);
  base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  return `${base.toString().replace(/\/$/, "")}/ws/voice/${conversationRef}`;
}

export class VoiceSession {
  private ws: WebSocket | null = null;
  private status: VoiceStatus = "closed";
  private closedByUser = false;
  private unsubscribeSession: (() => void) | null = null;

  // capture
  private stream: MediaStream | null = null;
  private captureCtx: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private decimate = 1;
  private sendRate = 24000;
  private talking = false;
  private voicedFrames = 0;
  private silenceMs = 0;
  private preroll: ArrayBuffer[] = [];

  // playback
  private playCtx: AudioContext | null = null;
  private playRate = 24000;
  private nextTime = 0;
  private carry: Uint8Array | null = null;
  private sources = new Set<AudioBufferSourceNode>();
  private currentResponse: string | null = null;
  private cancelled = new Set<string>();
  private responseDone = true;

  private readonly conversationRef: string;
  private readonly options: VoiceOptions;
  private readonly handlers: VoiceHandlers;

  constructor(conversationRef: string, options: VoiceOptions, handlers: VoiceHandlers) {
    this.conversationRef = conversationRef;
    this.options = { ...options };
    this.handlers = handlers;
  }

  get micAvailable(): boolean {
    return this.processor !== null;
  }

  get isTalking(): boolean {
    return this.talking;
  }

  setHandsFree(on: boolean): void {
    this.options.handsFree = on;
    if (!on && this.talking) this.stopTalking();
  }

  setThreshold(value: number): void {
    this.options.threshold = value;
  }

  // ---------------------------------------------------------------- lifecycle

  async connect(): Promise<void> {
    this.setStatus("connecting");
    // Create the playback context inside the user gesture that started the session,
    // otherwise browsers keep it suspended.
    this.playCtx = new AudioContext();
    void this.playCtx.resume();

    try {
      await this.startMicrophone();
    } catch (err) {
      this.handlers.onMicError?.(
        err instanceof Error && err.name === "NotAllowedError"
          ? "Microphone permission was denied. You can still type."
          : "No microphone available. You can still type.",
      );
    }

    const token = await accessToken();
    if (!token) {
      this.teardown();
      this.handlers.onClose({ code: 4401, reason: "", message: CLOSE_MESSAGES[4401] });
      return;
    }

    const ws = new WebSocket(wsUrl(this.conversationRef));
    ws.binaryType = "arraybuffer";
    this.ws = ws;

    ws.onopen = () => {
      const start: Record<string, unknown> = {
        type: "session.start",
        token,
        input_format: "pcm16",
        sample_rate: this.sendRate,
        output_format: "pcm",
      };
      if (this.options.voice) start.voice = this.options.voice;
      if (this.options.speed) start.speed = this.options.speed;
      ws.send(JSON.stringify(start));
    };
    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) this.playPcm(event.data);
      else this.onServerEvent(JSON.parse(event.data as string) as ServerEvent);
    };
    ws.onclose = (event) => {
      const message = this.closedByUser
        ? CLOSE_MESSAGES[1000]
        : CLOSE_MESSAGES[event.code] ?? "Voice connection lost.";
      this.teardown();
      this.handlers.onClose({ code: event.code, reason: event.reason, message });
    };

    // Keep the open session authorized past the access token's lifetime.
    this.unsubscribeSession = onSessionChange((s) => {
      if (s && this.ws?.readyState === WebSocket.OPEN) {
        this.sendJson({ type: "auth.refresh", token: s.access });
      }
    });
  }

  /** End the session cleanly (the server replies with session.closed + close 1000). */
  end(): void {
    this.closedByUser = true;
    if (this.ws?.readyState === WebSocket.OPEN) this.sendJson({ type: "session.end" });
    else this.ws?.close();
    // Don't wait on the server forever.
    const ws = this.ws;
    setTimeout(() => ws?.readyState !== WebSocket.CLOSED && ws?.close(), 2000);
  }

  private teardown(): void {
    this.unsubscribeSession?.();
    this.unsubscribeSession = null;
    this.talking = false;
    this.stopPlayback();
    this.stopMicrophone();
    void this.playCtx?.close().catch(() => {});
    this.playCtx = null;
    this.ws = null;
    this.setStatus("closed");
  }

  private setStatus(status: VoiceStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.handlers.onStatus(status);
  }

  private sendJson(message: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(message));
  }

  // ---------------------------------------------------------------- user actions

  startTalking(): void {
    if (this.talking || this.ws?.readyState !== WebSocket.OPEN || !this.micAvailable) return;
    this.talking = true;
    this.silenceMs = 0;
    this.voicedFrames = 0;
    this.stopPlayback(); // barge-in: silence the reply now; the server cancels it
    this.sendJson({ type: "speech.start" });
    for (const chunk of this.preroll) this.ws.send(chunk);
    this.preroll = [];
    this.setStatus("listening");
  }

  stopTalking(): void {
    if (!this.talking) return;
    this.talking = false;
    this.sendJson({ type: "speech.end" });
    this.setStatus("thinking");
  }

  sendText(text: string): void {
    this.stopPlayback();
    this.sendJson({ type: "text.message", text });
    this.setStatus("thinking");
  }

  cancelReply(): void {
    this.stopPlayback();
    this.sendJson({ type: "response.cancel" });
  }

  respondToConfirmation(confirmationId: UUID, decision: "approve" | "reject"): void {
    this.sendJson({ type: "confirmation.respond", confirmation_id: confirmationId, decision });
  }

  get isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN && this.status !== "connecting";
  }

  // ---------------------------------------------------------------- server events

  private onServerEvent(e: ServerEvent): void {
    switch (e.type) {
      case "session.ready":
        this.playRate = e.output_sample_rate || 24000;
        this.setStatus("idle");
        break;
      case "response.started":
        this.currentResponse = e.response_id;
        this.responseDone = false;
        if (!this.talking) this.setStatus("thinking");
        break;
      case "response.audio.started":
        this.playRate = e.sample_rate || this.playRate;
        break;
      case "response.done":
        this.responseDone = true;
        this.settleIfQuiet();
        break;
      case "response.cancelled":
        this.cancelled.add(e.response_id);
        this.stopPlayback();
        this.responseDone = true;
        this.settleIfQuiet();
        break;
      case "error":
        if (!e.fatal && !this.talking) {
          this.responseDone = true;
          this.settleIfQuiet();
        }
        break;
      default:
        break;
    }
    this.handlers.onEvent(e);
  }

  /** Back to idle once the reply has finished and nothing is still playing. */
  private settleIfQuiet(): void {
    if (this.talking) return;
    if (this.responseDone && this.sources.size === 0) this.setStatus("idle");
  }

  // ---------------------------------------------------------------- microphone

  private async startMicrophone(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("unsupported");
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const ctx = new AudioContext();
    this.captureCtx = ctx;
    const rate = ctx.sampleRate;
    // The server accepts pcm16 at 8–48 kHz; halve anything above that.
    this.decimate = rate > 48000 ? 2 : 1;
    this.sendRate = Math.round(rate / this.decimate);
    this.source = ctx.createMediaStreamSource(this.stream);
    this.processor = ctx.createScriptProcessor(4096, 1, 1);
    const mute = ctx.createGain();
    mute.gain.value = 0; // keep the graph running without echoing the mic
    this.source.connect(this.processor);
    this.processor.connect(mute);
    mute.connect(ctx.destination);
    this.processor.onaudioprocess = (ev) => this.onMicFrame(ev.inputBuffer.getChannelData(0));
  }

  private stopMicrophone(): void {
    this.processor?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.captureCtx?.close().catch(() => {});
    this.processor = null;
    this.source = null;
    this.stream = null;
    this.captureCtx = null;
    this.handlers.onLevel(0);
  }

  private toPcm16(samples: Float32Array): ArrayBuffer {
    const step = this.decimate;
    const out = new Int16Array(Math.floor(samples.length / step));
    for (let i = 0; i < out.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i * step]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out.buffer;
  }

  private onMicFrame(samples: Float32Array): void {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / samples.length);
    this.handlers.onLevel(rms);
    if (this.ws?.readyState !== WebSocket.OPEN || this.status === "connecting") return;

    const pcm = this.toPcm16(samples);
    if (this.options.handsFree && this.captureCtx) {
      this.handsFree(rms, (samples.length / this.captureCtx.sampleRate) * 1000);
    }
    if (this.talking) {
      this.ws.send(pcm);
    } else {
      this.preroll.push(pcm); // the moment before speech is detected, so words aren't clipped
      if (this.preroll.length > PREROLL_FRAMES) this.preroll.shift();
    }
  }

  private handsFree(rms: number, frameMs: number): void {
    const loud = rms > this.options.threshold;
    if (!this.talking) {
      this.voicedFrames = loud ? this.voicedFrames + 1 : 0;
      if (this.voicedFrames >= 2) this.startTalking();
    } else {
      this.silenceMs = loud ? 0 : this.silenceMs + frameMs;
      if (this.silenceMs > SILENCE_TO_END_MS) this.stopTalking();
    }
  }

  // ---------------------------------------------------------------- playback

  private playPcm(bytes: ArrayBuffer): void {
    const ctx = this.playCtx;
    if (!ctx) return;
    if (this.currentResponse && this.cancelled.has(this.currentResponse)) return;
    if (this.talking) return; // user is speaking over the reply

    let data = new Uint8Array(bytes);
    if (this.carry) {
      // Frames can split a 16-bit sample; keep the odd byte for the next one.
      const joined = new Uint8Array(this.carry.length + data.length);
      joined.set(this.carry);
      joined.set(data, this.carry.length);
      data = joined;
      this.carry = null;
    }
    if (data.length % 2) {
      this.carry = data.slice(-1);
      data = data.slice(0, -1);
    }
    if (!data.length) return;

    const pcm = new Int16Array(data.buffer, data.byteOffset, data.length / 2);
    const buffer = ctx.createBuffer(1, pcm.length, this.playRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 0x8000;

    const node = ctx.createBufferSource();
    node.buffer = buffer;
    node.connect(ctx.destination);
    const at = Math.max(this.nextTime, ctx.currentTime + 0.04);
    node.start(at);
    this.nextTime = at + buffer.duration;
    this.sources.add(node);
    this.setStatus("speaking");
    node.onended = () => {
      this.sources.delete(node);
      this.settleIfQuiet();
    };
  }

  private stopPlayback(): void {
    for (const s of this.sources) {
      s.onended = null;
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources.clear();
    this.nextTime = 0;
    this.carry = null;
  }
}
