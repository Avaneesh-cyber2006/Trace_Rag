import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from '../src/App';

const TOKEN = 'test-local-token';
const REPOS = { repositories: [{ repository_id: 'offline-demo', display_name: 'Offline demo' }] };
const ANSWER = {
  repository_id: 'offline-demo',
  answer: {
    status: 'answered', overview: '<img src=x onerror=alert(1)>',
    claims: [{ text: '<script>alert(1)</script>', category: 'indexed_fact', citations: ['S1'], excerpt: 'return 7' }],
    hypotheses: [{ summary: 'Could be static source.', supporting_citations: ['S1'],
      contradicting_citations: [], missing_observations: ['Live runtime capture'], status: 'possible' }],
    limitations: ['No runtime proof.'],
  },
  citations: [{ label: 'S1', kind: 'indexed_source', content: 'def calculate():\n    return 7\n',
    source_label: null, details: { relative_path: 'examples/demonstration.py' }, redacted: true }],
  warnings: ['Partial evidence.'],
};

function response(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } });
}
async function connect() {
  fireEvent.change(screen.getByLabelText('Session bearer token'), { target: { value: TOKEN } });
  fireEvent.click(screen.getByRole('button', { name: 'Connect to local API' }));
  await screen.findByRole('option', { name: 'Offline demo' });
}
async function submit() {
  fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'What does the code return?' } });
  fireEvent.click(screen.getByLabelText(/Approve this request.s external processing/i));
  fireEvent.click(screen.getByRole('button', { name: /Analyze question/ }));
  await screen.findByRole('tab', { name: 'Claims & limits' });
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => Promise.resolve(
    response(init.method === 'GET' ? REPOS : ANSWER),
  )));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('local evidence workbench', () => {
  it('requires an in-memory token and repository connection', () => {
    render(<App />);
    expect(screen.getByRole('button', { name: /Analyze question/ }).hasAttribute('disabled')).toBe(true);
    expect(screen.getByRole('option', { name: 'Connect to view repositories' })).toBeTruthy();
    expect(localStorage.length).toBe(0);
  });
  it('never sends an unapproved question', async () => {
    render(<App />); await connect();
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'Why?' } });
    expect(screen.getByRole('button', { name: /Analyze question/ }).hasAttribute('disabled')).toBe(true);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });
  it('renders model and source text as inert text, with working citation navigation', async () => {
    render(<App />); await connect(); await submit();
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeTruthy();
    expect(screen.getByText('<script>alert(1)</script>')).toBeTruthy();
    expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('script')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Open evidence S1' }));
    const evidence = screen.getByRole('tabpanel', { name: 'Evidence (1)' });
    expect(within(evidence).getByText(/return 7/)).toBeTruthy();
    expect(within(evidence).getByText('Redacted')).toBeTruthy();
  });
  it('shows hypotheses and missing observations', async () => {
    render(<App />); await connect(); await submit();
    fireEvent.click(screen.getByRole('tab', { name: 'Hypotheses (1)' }));
    expect(screen.getByText('Could be static source.')).toBeTruthy();
    expect(screen.getByText('Live runtime capture')).toBeTruthy();
  });
  it('preserves no source data when switching tokens', async () => {
    render(<App />); await connect(); await submit();
    fireEvent.change(screen.getByLabelText('Session bearer token'), { target: { value: 'other-secret-token' } });
    expect(screen.queryByText('<img src=x onerror=alert(1)>')).toBeNull();
    expect(screen.getByRole('button', { name: /Analyze question/ }).hasAttribute('disabled')).toBe(true);
    expect(localStorage.length).toBe(0);
  });
  it('displays a friendly error for server denial', async () => {
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => Promise.resolve(
      init.method === 'GET' ? response(REPOS) : response({ detail: { code: 'forbidden', secret: 'do-not-show' } }, 403),
    )));
    render(<App />); await connect();
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'Why?' } });
    fireEvent.click(screen.getByLabelText(/Approve this request.s external processing/i));
    fireEvent.click(screen.getByRole('button', { name: /Analyze question/ }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('not approved'));
    expect(screen.getByRole('alert').textContent).not.toContain('do-not-show');
  });
  it('keeps the bearer secret masked unless deliberately revealed', () => {
    render(<App />);
    const field = screen.getByLabelText('Session bearer token') as HTMLInputElement;
    expect(field.type).toBe('password');
    fireEvent.click(screen.getByRole('button', { name: 'Show session token' }));
    expect(field.type).toBe('text');
    fireEvent.click(screen.getByRole('button', { name: 'Hide session token' }));
    expect(field.type).toBe('password');
    expect(localStorage.length).toBe(0);
  });
  it('uses the case-file example to fill the existing question editor', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: /What does calculate return\?/i }));
    expect((screen.getByLabelText('Your question') as HTMLTextAreaElement).value).toBe('What does calculate return?');
    expect(document.activeElement).toBe(screen.getByLabelText('Your question'));
  });
  it('supports keyboard navigation between answer sections', async () => {
    render(<App />); await connect(); await submit();
    const tab = screen.getByRole('tab', { name: 'Claims & limits' });
    fireEvent.keyDown(tab, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: 'Evidence (1)' }).getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Evidence (1)' }), { key: 'End' });
    expect(screen.getByRole('tab', { name: 'Hypotheses (1)' }).getAttribute('aria-selected')).toBe('true');
  });
  it('supports an insufficient-evidence result without fabricated claims', async () => {
    const abstain = { ...ANSWER, answer: { status: 'insufficient_evidence', overview: 'No indexed source',
      claims: [], hypotheses: [], limitations: ['Evidence is incomplete'] }, citations: [], warnings: [] };
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => Promise.resolve(
      response(init.method === 'GET' ? REPOS : abstain),
    )));
    render(<App />); await connect(); await submit();
    expect(screen.getByText('No source-backed claim can be established from this evidence.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Open evidence/ })).toBeNull();
  });
});
