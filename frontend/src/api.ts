/** Strict, local-only HTTP adapter for the Module 10 FastAPI response contract. */
export type ClaimCategory = 'indexed_fact' | 'graph_relation' | 'reported_history' | 'reported_runtime' | 'hypothesis';
export type AnswerStatus = 'answered' | 'insufficient_evidence';
export type EvidenceKind = 'indexed_source' | 'revision_bound_graph' | 'unverified_git_history' | 'unverified_runtime';
export type HypothesisStatus = 'possible' | 'insufficient_evidence';

export interface Claim {
  text: string;
  category: ClaimCategory;
  citations: string[];
  excerpt: string | null;
}
export interface Hypothesis {
  summary: string;
  supporting_citations: string[];
  contradicting_citations: string[];
  missing_observations: string[];
  status: HypothesisStatus;
}
export interface Evidence {
  label: string;
  kind: EvidenceKind;
  content: string;
  source_label: string | null;
  details: Record<string, string>;
  redacted: boolean;
}
export interface AnswerEnvelope {
  repository_id: string;
  answer: { status: AnswerStatus; overview: string; claims: Claim[]; hypotheses: Hypothesis[]; limitations: string[] };
  citations: Evidence[];
  warnings: string[];
}
export interface Repository { repository_id: string; display_name: string }

const ERROR_MESSAGES: Record<string, string> = {
  unauthorized: 'Session token rejected. Check the local API token.',
  forbidden: 'The server or repository has not approved this request, or the origin is not allowed.',
  repository_not_found: 'That repository is no longer registered. Reconnect to refresh the list.',
  evidence_unavailable: 'The indexed evidence is unavailable or failed validation.',
  request_too_large: 'The question exceeded the server request limit.',
  unsupported_media_type: 'The server rejected the request format.',
  invalid_request: 'The question or request options are invalid.',
  rate_limited: 'Too many requests. Try again later.',
  generation_unavailable: 'The reasoning provider could not return a validated response.',
  internal_error: 'The local API returned an internal error.',
};
const MAX_RESPONSE_BYTES = 1_048_576;
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const CITATION_PATTERN = /^(S|G|H|R)[1-9][0-9]*$/;
const STATUS = ['answered', 'insufficient_evidence'];
const CATEGORIES = ['indexed_fact', 'graph_relation', 'reported_history', 'reported_runtime', 'hypothesis'];
const KINDS = ['indexed_source', 'revision_bound_graph', 'unverified_git_history', 'unverified_runtime'];

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function str(value: unknown, maximum = 32_768): value is string {
  return typeof value === 'string' && value.length <= maximum;
}
function strings(value: unknown, maxItems = 100): value is string[] {
  return Array.isArray(value) && value.length <= maxItems && value.every((item) => str(item, 4_096));
}
function oneOf(value: unknown, choices: string[]): boolean {
  return typeof value === 'string' && choices.includes(value);
}
function labels(value: unknown): value is string[] {
  return strings(value, 24) && value.every((item) => CITATION_PATTERN.test(item));
}
function parseClaim(value: unknown): value is Claim {
  return object(value) && str(value.text, 4_096) && oneOf(value.category, CATEGORIES) &&
    labels(value.citations) && (value.excerpt === null || str(value.excerpt, 4_096));
}
function parseHypothesis(value: unknown): value is Hypothesis {
  return object(value) && str(value.summary, 4_096) && oneOf(value.status, ['possible', 'insufficient_evidence']) &&
    labels(value.supporting_citations) && labels(value.contradicting_citations) &&
    strings(value.missing_observations, 16);
}
function parseEvidence(value: unknown): value is Evidence {
  return object(value) && typeof value.label === 'string' && CITATION_PATTERN.test(value.label) &&
    oneOf(value.kind, KINDS) && str(value.content, 65_536) &&
    (value.source_label === null || str(value.source_label, 512)) &&
    typeof value.redacted === 'boolean' && object(value.details) &&
    Object.keys(value.details).length <= 24 && Object.entries(value.details).every(([key, item]) => str(key, 64) && str(item, 2_048));
}

export function parseRepositories(value: unknown): Repository[] {
  if (!object(value) || !Array.isArray(value.repositories) || value.repositories.length > 100 ||
      !value.repositories.every((entry: unknown) => object(entry) &&
        typeof entry.repository_id === 'string' && ID_PATTERN.test(entry.repository_id) &&
        str(entry.display_name, 128) && entry.display_name.trim().length > 0)) {
    throw new Error('invalid_response');
  }
  const repos = value.repositories as Repository[];
  if (new Set(repos.map((item) => item.repository_id)).size !== repos.length) throw new Error('invalid_response');
  return repos;
}

export function parseEnvelope(value: unknown): AnswerEnvelope {
  if (!object(value) || typeof value.repository_id !== 'string' || !ID_PATTERN.test(value.repository_id) ||
      !object(value.answer) || !oneOf(value.answer.status, STATUS) ||
      !str(value.answer.overview, 8_192) || !Array.isArray(value.answer.claims) ||
      value.answer.claims.length > 12 || !value.answer.claims.every(parseClaim) ||
      !Array.isArray(value.answer.hypotheses) || value.answer.hypotheses.length > 8 ||
      !value.answer.hypotheses.every(parseHypothesis) || !strings(value.answer.limitations, 24) ||
      !Array.isArray(value.citations) || value.citations.length > 64 ||
      !value.citations.every(parseEvidence) || !strings(value.warnings, 24)) {
    throw new Error('invalid_response');
  }
  const envelope = value as unknown as AnswerEnvelope;
  const ids = new Set(envelope.citations.map((c) => c.label));
  if (ids.size !== envelope.citations.length) throw new Error('invalid_response');
  for (const claim of envelope.answer.claims) {
    if (claim.citations.some((citation) => !ids.has(citation))) throw new Error('invalid_response');
  }
  for (const hypothesis of envelope.answer.hypotheses) {
    if ([...hypothesis.supporting_citations, ...hypothesis.contradicting_citations]
      .some((citation) => !ids.has(citation))) throw new Error('invalid_response');
  }
  return envelope;
}

export class ApiError extends Error {
  constructor(public readonly code: string) { super(code); }
}

async function boundedJson(response: Response): Promise<unknown> {
  const lengthHeader = response.headers.get('content-length');
  if (lengthHeader !== null) {
    const knownSize = Number(lengthHeader);
    if (!Number.isSafeInteger(knownSize) || knownSize < 0 || knownSize > MAX_RESPONSE_BYTES) {
      throw new ApiError('invalid_response');
    }
  }
  // Enforce the limit *before* buffering an untrusted response body.
  // response.text() alone can allocate arbitrarily large data before checking.
  if (!response.body) throw new ApiError('invalid_response');
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let bytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_RESPONSE_BYTES) {
        await reader.cancel().catch(() => undefined);
        throw new ApiError('invalid_response');
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const buffer = new Uint8Array(bytes);
  let offset = 0;
  for (const chunk of chunks) {
    buffer.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(buffer)) as unknown;
  } catch {
    throw new ApiError('invalid_response');
  }
}

async function request<T>(url: string, token: string, init: RequestInit, parse: (value: unknown) => T, signal: AbortSignal): Promise<T> {
  if (!token || token.length > 512 || !token.trim()) throw new ApiError('unauthorized');
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/json', ...(init.headers ?? {}) },
      credentials: 'omit', cache: 'no-store', redirect: 'error', signal,
    });
  } catch (error) {
    if (signal.aborted) throw new ApiError('cancelled');
    throw new ApiError('connection_unavailable');
  }
  const payload = await boundedJson(response);
  if (!response.ok) {
    const code = object(payload) && object(payload.detail) && str(payload.detail.code, 64) ? payload.detail.code : 'unknown_error';
    throw new ApiError(code);
  }
  try { return parse(payload); } catch { throw new ApiError('invalid_response'); }
}

export function listRepositories(token: string, signal: AbortSignal): Promise<Repository[]> {
  return request('/api/v1/repositories', token, { method: 'GET' }, parseRepositories, signal);
}
export function askQuestion(token: string, repositoryId: string, question: string, topK: number, approved: boolean, signal: AbortSignal): Promise<AnswerEnvelope> {
  if (!approved || !ID_PATTERN.test(repositoryId) || !Number.isInteger(topK) || topK < 1 || topK > 8 ||
      new TextEncoder().encode(question).byteLength > 4_096 || !question.trim()) {
    throw new ApiError('invalid_request');
  }
  return request('/api/v1/answer', token, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repository_id: repositoryId, question, top_k: topK, approve_external_processing: true }),
  }, parseEnvelope, signal);
}
export function humanError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'cancelled') return '';
    if (error.code === 'connection_unavailable') return 'Cannot reach the local API. Check that the server is running on 127.0.0.1:8000.';
    if (error.code === 'invalid_response') return 'The local API returned an invalid or oversized response.';
    return ERROR_MESSAGES[error.code] ?? 'The local API could not complete the request.';
  }
  return 'The local API could not complete the request.';
}
