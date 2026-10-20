import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { ConnectionsPanel } from './ConnectionsPanel';
import { getConnections, getRequests } from '../../api/monitor';
import type { ConnectionInfo, RequestEntry } from '../../api/monitor';

vi.mock('../../api/monitor', () => ({
  getConnections: vi.fn(),
  getRequests: vi.fn(),
}));

const mockGetConnections = vi.mocked(getConnections);
const mockGetRequests = vi.mocked(getRequests);

const now = Date.now() / 1000;

const conn: ConnectionInfo = {
  ip: '10.0.0.5',
  count: 7,
  last_seen: now - 3,
  last_method: 'GET',
  last_path: '/api/v1/models',
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
  });

  afterEach(() => {
    cleanup();
  });

  it('lists the IP with its request count', async () => {
    renderPanel();
    await screen.findByText('10.0.0.5');
    expect(screen.getByText('7')).toBeTruthy();
    expect(screen.getByText('Connections')).toBeTruthy();
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
});
