import { describe, expect, it, vi } from 'vitest';
import { askQuestion, humanError, listRepositories, parseEnvelope, parseRepositories } from '../src/api';

const controller = () => new AbortController().signal;
function response(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } });
}
const good = {
  repository_id: 'offline-demo',
  answer: {
    status: 'answered', overview: '<img src=x onerror=alert(1)>',
    claims: [{ text: 'Indexed fact', category: 'indexed_fact', citations: ['S1'], excerpt: null }],
    hypotheses: [], limitations: ['Execution not verified'],
  },
  citations: [{ label: 'S1', kind: 'indexed_source', content: '<script>bad</script>',
    source_label: null, details: {}, redacted: false }], warnings: [],
};

describe('strict response parsing', () => {
  it('accepts valid repository metadata without paths', () => {
    expect(parseRepositories({ repositories: [{ repository_id: 'offline-demo', display_name: 'Demo' }] })).toHaveLength(1);
  });
  it('rejects untrusted or duplicate repository identifiers', () => {
    expect(() => parseRepositories({ repositories: [{ repository_id: '../../etc', display_name: 'Oops' }] })).toThrow();
    expect(() => parseRepositories({ repositories: [
      { repository_id: 'x', display_name: 'A' }, { repository_id: 'x', display_name: 'B' },
    ] })).toThrow();
  });
  it('rejects fabricated citations and malformed evidence types', () => {
    expect(() => parseEnvelope({ ...good, citations: [] })).toThrow();
    expect(() => parseEnvelope({ ...good, citations: [{ ...good.citations[0], details: { private: {} } }] })).toThrow();
    expect(() => parseEnvelope({ ...good, answer: { ...good.answer, status: 'confirmed_root_cause' } })).toThrow();
  });
  it('rejects a misleading oversized content-length before consuming the body', async () => {
    const fake = vi.fn().mockResolvedValue(new Response(JSON.stringify({ repositories: [] }), {
      headers: { 'content-type': 'application/json', 'content-length': '1048577' },
    }));
    vi.stubGlobal('fetch', fake);
    await expect(listRepositories('local-secret', controller())).rejects.toThrow('invalid_response');
    vi.unstubAllGlobals();
  });
  it('stops reading an oversized streamed response before decoding JSON', async () => {
    const oversized = new Uint8Array(1_048_577);
    const stream = new ReadableStream<Uint8Array>({
      start(streamController) { streamController.enqueue(oversized); streamController.close(); },
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream)));
    await expect(listRepositories('local-secret', controller())).rejects.toThrow('invalid_response');
    vi.unstubAllGlobals();
  });
  it('keeps code and model text as data, never markup', () => {
    expect(parseEnvelope(good).citations[0].content).toBe('<script>bad</script>');
  });
});

describe('local API requests', () => {
  it('uses a bearer header and excludes browser credentials', async () => {
    const fake = vi.fn().mockResolvedValue(response({ repositories: [] }));
    vi.stubGlobal('fetch', fake);
    await listRepositories('local-secret', controller());
    expect(fake).toHaveBeenCalledTimes(1);
    const [url, init] = fake.mock.calls[0];
    expect(url).toBe('/api/v1/repositories');
    expect(init.headers.Authorization).toBe('Bearer local-secret');
    expect(init.credentials).toBe('omit');
    expect(init.cache).toBe('no-store');
    vi.unstubAllGlobals();
  });
  it('only sends approved repository ID, question and bounded top_k', async () => {
    const fake = vi.fn().mockResolvedValue(response(good));
    vi.stubGlobal('fetch', fake);
    await askQuestion('local-secret', 'offline-demo', 'Why?', 5, true, controller());
    const [url, init] = fake.mock.calls[0];
    expect(url).toBe('/api/v1/answer');
    expect(JSON.parse(init.body)).toEqual({
      repository_id: 'offline-demo', question: 'Why?', top_k: 5, approve_external_processing: true,
    });
    vi.unstubAllGlobals();
  });
  it('rejects missing approval before fetch', () => {
    const fake = vi.fn(); vi.stubGlobal('fetch', fake);
    expect(() => askQuestion('local-secret', 'offline-demo', 'Why?', 5, false, controller())).toThrow();
    expect(fake).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
  it('never renders raw server error or provider traces', async () => {
    const secret = 'local-super-private-sdk-stacktrace';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ detail: { code: 'generation_unavailable', secret } }, 502)));
    await expect(askQuestion('local-secret', 'offline-demo', 'Why?', 5, true, controller()))
      .rejects.toThrow('generation_unavailable');
    expect(humanError(new Error(secret))).not.toContain(secret);
    vi.unstubAllGlobals();
  });
  it('does not disclose network exception content', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('socket detail with secret')));
    await expect(listRepositories('local-secret', controller())).rejects.toThrow('connection_unavailable');
    vi.unstubAllGlobals();
  });
});
