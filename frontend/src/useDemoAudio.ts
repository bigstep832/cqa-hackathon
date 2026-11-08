import { useCallback, useEffect, useRef, useState } from 'react';
import type { ConversationVersion } from './api/client';

type Track = {
  version: string; hash: string; file: string; duration: number;
  cues: Record<string, number>; peaks: number[];
};

export function audioTime(time: number) {
  const seconds = Math.floor(time);
  return Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
}

export function useDemoAudio(conversation: ConversationVersion) {
  const eligible = conversation.synthetic && conversation.id === 'synthetic-conversation-v1'
    && conversation.conversation_id === 'synthetic-example-001' && !conversation.audio_url;
  const [manifest, setManifest] = useState<Track | null>(null);
  const [failed, setFailed] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const element = useRef<HTMLAudioElement>(null);
  const attach = useCallback((node: HTMLAudioElement | null) => { element.current = node; }, []);
  useEffect(() => {
    if (!eligible) return;
    const controller = new AbortController();
    async function load() {
      const response = await fetch('/demo-audio/track.json', { signal: controller.signal });
      if (!response.ok) throw new Error('Track unavailable');
      const data: Track = await response.json();
      if (data.version !== conversation.id || data.hash !== conversation.content_hash
          || !/^chat-[a-f0-9]{16}\.wav$/.test(data.file) || !(data.duration > 0)
          || !Array.isArray(data.peaks) || !data.peaks.length
          || !conversation.messages.every(m => Number.isFinite(data.cues[m.message_id])
            && data.cues[m.message_id]! >= 0 && data.cues[m.message_id]! < data.duration)) {
        throw new Error('Track does not match the transcript');
      }
      if (!controller.signal.aborted) setManifest(data);
    }
    void load().catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [eligible, conversation.id, conversation.content_hash, conversation.messages, attempt]);

  const track = eligible && manifest?.hash === conversation.content_hash ? manifest : null;
  function seek(seconds: number) {
    if (!element.current || !track) return;
    element.current.currentTime = Math.min(track.duration, Math.max(0, seconds));
    setPosition(element.current.currentTime);
  }
  function play(seconds?: number) {
    const audio = element.current;
    if (!audio || failed) return;
    if (seconds !== undefined) seek(seconds);
    void audio.play().catch((error: Error) => {
      if (element.current === audio && error.name !== 'AbortError') setFailed(true);
    });
  }
  return { eligible, attach, pause: () => element.current?.pause(), track, playing, failed, position, seek, play, setPlaying, setPosition,
    setFailed, retry: () => { setFailed(false); setPlaying(false); setPosition(0); setManifest(null); setAttempt(value => value + 1); } };
}
