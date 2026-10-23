import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { ConnectionsPanel } from './ConnectionsPanel';
import { getConnections, getRequests, setConnectionNote } from '../../api/monitor';
import type { ConnectionInfo, RequestEntry } from '../../api/monitor';

vi.mock('../../api/monitor', () => ({
  getConnections: vi.fn(),
  getRequests: vi.fn(),
  setConnectionNote: vi.fn(),
}));

const mockGetConnections = vi.mocked(getConnections);
const mockGetRequests = vi.mocked(getRequests);
const mockSetConnectionNote = vi.mocked(setConnectionNote);

const now = Date.now() / 1000;

const conn: ConnectionInfo = {
  ip: '10.0.0.5',
  count: 7,
  last_seen: now - 3,
  last_method: 'GET',
  last_path: '/api/v1/models',
  note: '',
};

const entry: RequestEntry = {
  ts: now - 3,
  ip: '10.0.0.5',
  method: 'POST',
  path: '/api/v1/models/chat',
  status: 200,
  body: 'prompt here',
};

function renderPanel() {
  return render(
    <I18nProvider>
      <ConnectionsPanel />
    </I18nProvider>,
  );
}

describe('ConnectionsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetConnections.mockResolvedValue([conn]);
    mockGetRequests.mockResolvedValue([entry]);
    mockSetConnectionNote.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
  });

  it('lists the IP with its request count', async () => {
    renderPanel();
    await screen.findByText('10.0.0.5');
    expect(screen.getByText('7')).toBeTruthy();
    // No in-panel header title — the Console tab bar already labels the panel.
    expect(screen.queryByText('Connections')).toBeNull();
  });

  it('opens the details modal with the IP\'s request content', async () => {
    renderPanel();
    await screen.findByText('10.0.0.5');
    screen.getByRole('button', { name: 'Details' }).click();
    await screen.findByText('prompt here');
    expect(mockGetRequests).toHaveBeenCalledWith('10.0.0.5');
  });

  it('shows the empty state when no connections are recorded', async () => {
    mockGetConnections.mockResolvedValue([]);
    renderPanel();
    await screen.findByText('No requests yet');
  });

  it('shows a relative last-seen time', async () => {
    renderPanel();
    await screen.findByText('Just now');
  });

  it('shows a saved note in the Notes column', async () => {
    mockGetConnections.mockResolvedValue([{ ...conn, note: 'my box' }]);
    renderPanel();
    await screen.findByText('my box');
    expect(screen.getByText('Notes')).toBeTruthy();
  });

  it('shows a + affordance (no placeholder text) in empty note cells', async () => {
    renderPanel();
    const btn = await screen.findByRole('button', { name: 'Notes' });
    // conn.note is '' — the cell must stay text-free (user ruling: no
    // placeholder) while staying a visible, clickable target.
    expect(btn.textContent).toBe('');
    expect(btn.querySelector('svg')).toBeTruthy();
  });

  it('saves the note when Enter is pressed in the note input', async () => {
    renderPanel();
    await screen.findByText('10.0.0.5');
    fireEvent.click(screen.getByRole('button', { name: 'Notes' }));
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'my box' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(mockSetConnectionNote).toHaveBeenCalledWith('10.0.0.5', 'my box'));
  });

  it('discards the note draft on Escape', async () => {
    renderPanel();
    await screen.findByText('10.0.0.5');
    fireEvent.click(screen.getByRole('button', { name: 'Notes' }));
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'draft' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(mockSetConnectionNote).not.toHaveBeenCalled();
  });
});
