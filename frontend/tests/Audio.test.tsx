import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { ConversationAudio } from '../src/ConversationAudio';
import { ReviewWorkspace } from '../src/ReviewWorkspace';
import { originalResult } from './appealFixtures';

describe('Аудио в карточке версии', () => {
  it('не запрашивает отсутствующую запись и загружает существующую только по нажатию', async () => {
    const view = render(<ConversationAudio conversation={{ id: 'old /?#', audio_url: null }} />);
    expect(screen.getByText('Аудиозапись отсутствует')).toBeVisible();
    expect(document.querySelector('audio')).toBeNull();
    view.rerender(<ConversationAudio conversation={{ id: 'old /?#', audio_url: 'https://example.invalid/upstream-private' }} />);
    expect(document.querySelector('audio')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Загрузить аудиозапись' }));
    expect(screen.getByLabelText('Проигрыватель записи звонка')).toHaveAttribute('src', '/api/v1/conversation-versions/old%20%2F%3F%23/audio');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
  it('отображает загрузку, готовность, воспроизведение, паузу и повтор после ошибки', async () => {
    render(<ConversationAudio conversation={{ id: 'old-version', audio_url: '/api/v1/conversation-versions/old-version/audio' }} />);
    await userEvent.click(screen.getByRole('button', { name: 'Загрузить аудиозапись' }));
    expect(screen.getByRole('status')).toHaveTextContent('Загружаем');
    const audio = screen.getByLabelText('Проигрыватель записи звонка');
    fireEvent.loadedMetadata(audio); expect(screen.getByRole('status')).toHaveTextContent('Аудиозапись доступна');
    fireEvent.playing(audio); expect(screen.getByRole('status')).toHaveTextContent('Воспроизводится');
    fireEvent.pause(audio); expect(screen.getByRole('status')).toHaveTextContent('приостановлено');
    fireEvent.error(audio); expect(screen.getByRole('alert')).toHaveTextContent('Не удалось загрузить или воспроизвести');
    await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку аудио' }));
    const retry = screen.getByLabelText('Проигрыватель записи звонка');
    expect(retry).not.toBe(audio); expect(retry).toHaveAttribute('src', '/api/v1/conversation-versions/old-version/audio');
    fireEvent.loadedMetadata(retry); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
  it('сохраняет транскрипт при ошибке и не использует ISO timestamp для seek evidence', async () => {
    const data = originalResult();
    data.conversation = { ...data.conversation, channel: 'voice call', audio_url: '/present', messages: data.conversation.messages.map(item => ({ ...item, timestamp_raw: '2026-11-08T03:01:15Z', audio_offset_seconds: null })) };
    render(<ReviewWorkspace data={data} />);
    await userEvent.click(screen.getByRole('button', { name: 'Загрузить аудиозапись' }));
    const audio = screen.getByLabelText('Проигрыватель записи звонка') as HTMLAudioElement;
    fireEvent.loadedMetadata(audio);
    await userEvent.click(screen.getByRole('button', { name: 'Показать доказательство C01' }));
    expect(document.querySelector('.message--highlighted')).toHaveFocus(); expect(audio.currentTime).toBe(0);
    fireEvent.error(audio);
    expect(screen.getByRole('alert')).toHaveTextContent('Транскрипт остаётся доступным');
    expect(screen.getByText(data.conversation.messages[0]!.text, { exact: true })).toBeVisible();
    expect(screen.getByTestId('total-score')).toHaveTextContent('85,38');
  });
  it('смена версии сбрасывает состояние плеера и не продолжает старую запись', async () => {
    const view = render(<ConversationAudio conversation={{ id: 'version-one', audio_url: '/present' }} />);
    await userEvent.click(screen.getByRole('button', { name: 'Загрузить аудиозапись' }));
    const old = screen.getByLabelText('Проигрыватель записи звонка'); fireEvent.playing(old);
    view.rerender(<ConversationAudio conversation={{ id: 'version-two', audio_url: '/present' }} />);
    expect(old).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Загрузить аудиозапись' }));
    expect(screen.getByLabelText('Проигрыватель записи звонка')).toHaveAttribute('src', '/api/v1/conversation-versions/version-two/audio');
  });
});
