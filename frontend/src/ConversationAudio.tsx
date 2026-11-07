import { useState } from 'react';
import type { ConversationVersion } from './api/client';

export function ConversationAudio({ conversation }: { conversation: Pick<ConversationVersion, 'id' | 'audio_url'> }) {
  if (!conversation.audio_url) return <span>Аудиозапись отсутствует</span>;
  // Use the same backend proxy as the API client, bound to the displayed snapshot.
  // Never expose the upstream URL or derive seek positions from message timestamps.
  return <AudioPlayer key={conversation.id} source={`/api/v1/conversation-versions/${encodeURIComponent(conversation.id)}/audio`} />;
}

function AudioPlayer({ source }: { source: string }) {
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'playing' | 'paused' | 'error'>('idle');
  return <section className="conversation-audio" aria-label="Аудиозапись звонка">
    {state === 'idle' ? <button className="secondary-button" onClick={() => setState('loading')}>Загрузить аудиозапись</button>
      : state === 'error' ? <div role="alert">
        <p>Не удалось загрузить или воспроизвести аудиозапись. Проверьте подключение или повторите загрузку позже.</p>
        <p>Транскрипт остаётся доступным.</p>
        <button className="secondary-button" onClick={() => setState('loading')}>Повторить загрузку аудио</button>
      </div> : <>
        <audio controls preload="metadata" src={source} aria-label="Проигрыватель записи звонка"
          onLoadedMetadata={() => setState(current => current === 'loading' ? 'ready' : current)}
          onPlaying={() => setState('playing')}
          onPause={() => setState(current => current === 'playing' ? 'paused' : current)}
          onEnded={() => setState('ready')}
          onError={() => setState('error')} />
        <p role="status">{state === 'loading' ? 'Загружаем аудиозапись…' : state === 'playing' ? 'Воспроизводится аудиозапись.'
          : state === 'paused' ? 'Воспроизведение приостановлено.' : 'Аудиозапись доступна.'}</p>
      </>}
  </section>;
}
