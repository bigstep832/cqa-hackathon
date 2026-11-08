import { audioTime, type useDemoAudio } from './useDemoAudio';

export function DemoAudio({ player }: { player: ReturnType<typeof useDemoAudio> }) {
  const { track, playing, position } = player;
  return <section className="panel narration" aria-label="Аудиодорожка диалога">
    <h2>Аудиодорожка диалога</h2>
    <p>Синтетическая озвучка чата. Чтобы послушать реплику, нажмите ▶ рядом с её текстом.</p>
    {player.failed ? <div role="alert">Аудиодорожка недоступна. <button className="text-link" onClick={player.retry}>Повторить</button></div>
      : !track ? <span role="status">Загружаем дорожку…</span> : <>
        <audio ref={node => player.attach(node)} preload="metadata" src={'/demo-audio/' + track.file}
          onPlaying={() => player.setPlaying(true)} onPause={() => player.setPlaying(false)}
          onEnded={() => player.setPlaying(false)} onError={() => player.setFailed(true)}
          onTimeUpdate={event => player.setPosition(event.currentTarget.currentTime)} />
        <div className="narration-controls">
          <button className="secondary-button" onClick={() => playing ? player.pause() : player.play()}>
            {playing ? 'Пауза' : 'Слушать'}</button>
          <div className="narration-wave">
            <svg viewBox="0 0 150 44" preserveAspectRatio="none" aria-hidden="true">
              {track.peaks.map((peak, index) => {
                const height = Math.max(1.5, peak * 40);
                return <rect key={index} x={150 * index / track.peaks.length} y={(44 - height) / 2}
                  width={110 / track.peaks.length} height={height} rx=".3"
                  fill={index / track.peaks.length < position / track.duration ? 'var(--green)' : 'var(--control-border)'} />;
              })}
              <line x1={150 * position / track.duration} x2={150 * position / track.duration}
                y1="0" y2="44" stroke="var(--ink)" strokeWidth=".5" />
            </svg>
            <input type="range" aria-label="Позиция аудиодорожки" min={0} max={track.duration} step={0.1}
              value={position} aria-valuetext={audioTime(position)} onChange={event => player.seek(Number(event.currentTarget.value))} />
          </div>
          <span className="narration-time">{audioTime(position)} / {audioTime(track.duration)}</span>
        </div>
      </>}
  </section>;
}
