import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { askQuestion, humanError, listRepositories } from './api';
import type { AnswerEnvelope, Evidence, Repository } from './api';

type View = 'answer' | 'evidence' | 'hypotheses';
const VIEWS: View[] = ['answer', 'evidence', 'hypotheses'];
const EXAMPLE_QUESTIONS = [
  'What does calculate return?',
  'What could cause an unexpected return value?',
];
const KIND_NAMES: Record<string, string> = {
  indexed_source: 'Indexed source',
  revision_bound_graph: 'Revision-bound graph',
  unverified_git_history: 'Git history · unverified',
  unverified_runtime: 'Runtime observation · unverified',
};
const STATUS_LABELS: Record<string, string> = {
  answered: 'Cited answer', insufficient_evidence: 'Insufficient evidence',
};

function CitationLinks({ labels, onSelect }: { labels: string[]; onSelect: (label: string) => void }) {
  if (labels.length === 0) return <span className="muted">No cited evidence</span>;
  return <span className="citation-links">{labels.map((label) =>
    <button className="citation-pill" type="button" key={label} onClick={() => onSelect(label)}
      aria-label={`Open evidence ${label}`}>{label}<span aria-hidden="true"> ↗</span></button>,
  )}</span>;
}

function EvidenceCard({ item, active }: { item: Evidence; active: boolean }) {
  return <article id={`evidence-${item.label}`} className={`evidence-card ${active ? 'evidence-active' : ''}`}
    tabIndex={-1} aria-label={`Evidence ${item.label}`}>
    <div className="evidence-topline">
      <div className="evidence-heading"><span className="evidence-id">{item.label}</span>
        <span className="evidence-kind">{KIND_NAMES[item.kind] ?? 'Evidence'}</span></div>
      {item.redacted && <span className="privacy-tag">Redacted</span>}
    </div>
    {item.source_label && <p className="source-link">Related to {item.source_label}</p>}
    {Object.entries(item.details).length > 0 && <dl className="detail-grid">
      {Object.entries(item.details).map(([key, value]) => <div key={key}>
        <dt>{key.replaceAll('_', ' ')}</dt><dd>{value}</dd>
      </div>)}
    </dl>}
    <pre className="code-excerpt"><code>{item.content}</code></pre>
    {(item.kind === 'unverified_git_history' || item.kind === 'unverified_runtime') &&
      <p className="evidence-disclaimer">Observation only. This clue does not prove a root cause.</p>}
  </article>;
}

export default function App() {
  const [token, setToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [repositoryId, setRepositoryId] = useState('');
  const [question, setQuestion] = useState('');
  const [topK, setTopK] = useState(5);
  const [approved, setApproved] = useState(false);
  const [result, setResult] = useState<AnswerEnvelope | null>(null);
  const [view, setView] = useState<View>('answer');
  const [activeCitation, setActiveCitation] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<'connect' | 'answer' | null>(null);
  const activeRequest = useRef<AbortController | null>(null);
  const questionRef = useRef<HTMLTextAreaElement | null>(null);

  const cancelRequest = () => {
    activeRequest.current?.abort();
    activeRequest.current = null;
    setBusy(null);
  };
  useEffect(() => () => { activeRequest.current?.abort(); }, []);

  const clearAnswer = () => { setResult(null); setActiveCitation(''); setView('answer'); setError(''); };
  const changeToken = (value: string) => {
    cancelRequest(); setToken(value); setRepositories([]); setRepositoryId(''); setApproved(false); clearAnswer();
  };
  const changeRepository = (value: string) => {
    cancelRequest(); setRepositoryId(value); setApproved(false); clearAnswer();
  };
  const runRequest = async (type: 'connect' | 'answer', run: (signal: AbortSignal) => Promise<void>) => {
    cancelRequest();
    const controller = new AbortController();
    activeRequest.current = controller;
    setBusy(type); setError('');
    try { await run(controller.signal); }
    catch (failure) { if (!controller.signal.aborted) setError(humanError(failure)); }
    finally {
      if (activeRequest.current === controller) { activeRequest.current = null; setBusy(null); }
    }
  };
  const connect = () => runRequest('connect', async (signal) => {
    const available = await listRepositories(token, signal);
    if (signal.aborted) return;
    setRepositories(available); setRepositoryId(available.length === 1 ? available[0].repository_id : '');
    setApproved(false); clearAnswer();
  });
  const answer = () => {
    if (!repositoryId || !approved || !question.trim() || busy) return;
    void runRequest('answer', async (signal) => {
      const response = await askQuestion(token, repositoryId, question, topK, approved, signal);
      if (signal.aborted) return;
      if (response.repository_id !== repositoryId) throw new Error('invalid_response');
      setResult(response); setView('answer'); setActiveCitation('');
    });
  };
  const openCitation = (label: string) => {
    if (!result?.citations.some((citation) => citation.label === label)) return;
    setActiveCitation(label); setView('evidence');
  };
  const chooseExample = (example: string) => {
    cancelRequest(); setQuestion(example); clearAnswer(); questionRef.current?.focus();
  };
  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, current: View) => {
    const index = VIEWS.indexOf(current);
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % VIEWS.length;
    else if (event.key === 'ArrowLeft') next = (index + VIEWS.length - 1) % VIEWS.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = VIEWS.length - 1;
    else return;
    event.preventDefault();
    setView(VIEWS[next]);
    document.getElementById(`tab-${VIEWS[next]}`)?.focus();
  };
  useEffect(() => {
    if (view === 'evidence' && activeCitation) {
      const target = document.getElementById(`evidence-${activeCitation}`);
      target?.focus({ preventScroll: true });
      target?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    }
  }, [view, activeCitation]);
  const count = new TextEncoder().encode(question).byteLength;
  const eligible = Boolean(repositoryId && question.trim() && count <= 4_096 && approved && !busy);
  const selectedRepo = repositories.find((item) => item.repository_id === repositoryId);

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand" aria-label="TraceRAG">
        <div className="brand-symbol" aria-hidden="true">T<span>R</span></div>
        <div className="brand-name"><strong>TraceRAG</strong><small>CAUSAL CODE INTELLIGENCE</small></div>
      </div>
      <div className="topbar-center" aria-hidden="true"><span className="topbar-line" /> INVESTIGATION STUDIO <span className="topbar-line" /></div>
      <div className="environment"><span className="pulse" aria-hidden="true" /> LOCAL WORKSPACE <span className="version">MODULE / 10</span></div>
    </header>

    <div className="workspace">
      <aside className="sidebar" aria-label="Investigation controls">
        <div className="sidebar-head">
          <div className="eyebrow"><span className="eyebrow-mark" /> NEW INVESTIGATION</div>
          <h1>Follow the code.<br /><em>Find the reason.</em></h1>
          <p>Start with a question. We'll trace the indexed evidence and show which claims it can actually support.</p>
        </div>

        <section className="control-section" aria-labelledby="session-heading">
          <div className="section-title"><span className="section-number">01</span>
            <h2 id="session-heading">Connect your workspace</h2>
            {repositories.length > 0 && <span className="connected-label">Connected</span>}
          </div>
          <label htmlFor="bearer-token">Session bearer token</label>
          <div className="token-field">
            <input id="bearer-token" type={showToken ? 'text' : 'password'} value={token}
              onChange={(e) => changeToken(e.target.value)} autoComplete="off" spellCheck={false}
              placeholder="Paste your local API token" maxLength={512} />
            <button type="button" className="token-visibility" aria-label={showToken ? 'Hide session token' : 'Show session token'}
              aria-pressed={showToken} onClick={() => setShowToken(!showToken)}>{showToken ? 'Hide' : 'Show'}</button>
          </div>
          <p className="helper">Only kept in page memory, never browser storage.</p>
          <button className="secondary-btn" type="button" onClick={() => void connect()} disabled={!token.trim() || busy !== null}>
            {busy === 'connect' ? 'Connecting…' : repositories.length ? 'Reconnect to local API' : 'Connect to local API'}
            <span aria-hidden="true">↗</span>
          </button>
        </section>

        <section className="control-section" aria-labelledby="repo-heading">
          <div className="section-title"><span className="section-number">02</span><h2 id="repo-heading">Choose a repository</h2></div>
          <label htmlFor="repository">Registered repository</label>
          <select id="repository" value={repositoryId} onChange={(e) => changeRepository(e.target.value)}
            disabled={!repositories.length || busy !== null}>
            <option value="">{repositories.length ? 'Select repository' : 'Connect to view repositories'}</option>
            {repositories.map((repo) => <option key={repo.repository_id} value={repo.repository_id}>{repo.display_name}</option>)}
          </select>
          <p className="helper">{repositories.length > 0 ? `${repositories.length} server-registered repository${repositories.length === 1 ? '' : 'ies'} available.` : 'Only repositories approved by your local server appear here.'}</p>
        </section>

        <section className="control-section" aria-labelledby="query-heading">
          <div className="section-title"><span className="section-number">03</span><h2 id="query-heading">Ask your question</h2></div>
          <label htmlFor="question">Your question</label>
          <textarea ref={questionRef} id="question" rows={5} value={question}
            onChange={(e) => { cancelRequest(); setQuestion(e.target.value); clearAnswer(); }}
            placeholder="Why can this function return an unexpected value?" spellCheck={false} maxLength={4_096} />
          <div className="field-meta"><span>Be specific about a behavior or possible cause.</span>
            <span className={count > 4_096 ? 'over-limit' : ''}>{count}/4,096 bytes</span></div>
          <label htmlFor="top-k">Indexed source results</label>
          <select id="top-k" value={topK} onChange={(e) => { cancelRequest(); setTopK(Number(e.target.value)); clearAnswer(); }}>
            {[1, 3, 5, 8].map((n) => <option key={n} value={n}>{n} source hit{n === 1 ? '' : 's'}</option>)}
          </select>
        </section>

        <section className="consent-section" aria-label="External processing approval">
          <label className="consent-check" htmlFor="consent">
            <input id="consent" type="checkbox" checked={approved}
              onChange={(e) => { cancelRequest(); setApproved(e.target.checked); clearAnswer(); }} />
            <span><strong>Approve this request's external processing</strong>
              <small>The selected server provider may transmit redacted source excerpts and your question outside this machine. Approval is required for this request.</small></span>
          </label>
          <p className="helper">The server and repository must also permit transmission. The offline demo never contacts Gemini.</p>
        </section>
        {error && <div className="error-box" role="alert">{error}</div>}
        <button className="primary-btn" type="button" onClick={answer} disabled={!eligible}>
          <span>{busy === 'answer' ? 'Analyzing…' : 'Analyze question'}</span><span aria-hidden="true">↗</span>
        </button>
        {busy && <button className="cancel-btn" type="button" onClick={cancelRequest}>Cancel request</button>}
        <p className="sidebar-footnote">PRIVATE BY DEFAULT <span aria-hidden="true">·</span> EVIDENCE FIRST</p>
      </aside>

      <main className="main-panel" aria-label="TraceRAG analysis workbench">
        <div className="main-heading">
          <div><div className="eyebrow main-eyebrow">WORKBENCH <span aria-hidden="true">/</span> CASE FILE</div><h2>Evidence workbench<span className="heading-period">.</span></h2>
            <p>Follow every conclusion back to what the repository actually shows.</p></div>
          <span className="context-badge"><span aria-hidden="true">◆</span> Source-backed · Not runtime proof</span>
        </div>
        <div className="process-rail" aria-label="Analysis process">
          <div><span className="rail-node">01</span><span>Retrieve <small>Indexed source</small></span></div>
          <div><span className="rail-node">02</span><span>Reason <small>Evidence boundaries</small></span></div>
          <div><span className="rail-node">03</span><span>Cite <small>Verifiable references</small></span></div>
        </div>

        {!result && <section className="empty-state" role="status" aria-live="polite" aria-busy={busy === 'answer'}>
          <div className="empty-corner">CASE FILE <span>—</span> 000</div>
          <div className="empty-icon" aria-hidden="true"><span className="empty-icon-vertical" /> <span className="empty-icon-horizontal" /> <b>S</b><i>?</i></div>
          <div className="empty-copy"><span className="section-kicker">{busy === 'answer' ? 'ANALYSIS IN PROGRESS' : 'READY WHEN YOU ARE'}</span>
            <h3>{busy === 'answer' ? 'Following the evidence…' : <>Start with a question.<br /><em>Leave with a trace.</em></>}</h3>
            <p>{busy === 'answer' ? 'Retrieving source evidence and checking every citation before it can appear in an answer.' : 'Connect a repository, ask what changed or why something behaves a certain way, then inspect the exact evidence behind the answer.'}</p>
            {!busy && <div className="prompt-examples"><span>TRY A QUESTION</span>
              {EXAMPLE_QUESTIONS.map((example) => <button type="button" key={example} onClick={() => chooseExample(example)}>
                {example}<span aria-hidden="true">↗</span>
              </button>)}
            </div>}
          </div>
          <div className="empty-footer">NO ASSUMPTIONS <span>·</span> NO FABRICATED CITATIONS</div>
        </section>}

        {result && <section className="result-area" aria-label="Analysis result">
          <div className="result-summary"><div className="result-status"><span className={`status-dot ${result.answer.status}`} />
            {STATUS_LABELS[result.answer.status]}</div>
            <div className="result-overview"><div className="result-index">FINDING <span>/</span> 01</div>
              <h3>{result.answer.overview}</h3></div>
            <div className="result-stats" aria-label="Result counts"><span><strong>{result.citations.length}</strong> evidence items</span>
              <span><strong>{result.answer.claims.length}</strong> claims</span>
              <span><strong>{result.answer.hypotheses.length}</strong> hypotheses</span></div>
          </div>
          {selectedRepo && <p className="result-context">Repository <strong>{selectedRepo.display_name}</strong> <span aria-hidden="true">/</span> Source-backed analysis, not a runtime verdict</p>}
          {result.warnings.length > 0 && <div className="warning-panel"><strong>Evidence notes</strong>
            {result.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
          </div>}
          <div role="tablist" aria-label="Analysis sections" className="result-tabs">
            {VIEWS.map((item) => <button key={item} type="button" role="tab" id={`tab-${item}`}
              tabIndex={view === item ? 0 : -1} aria-selected={view === item} aria-controls={`panel-${item}`}
              onKeyDown={(event) => onTabKeyDown(event, item)}
              onClick={() => setView(item)}>{item === 'answer' ? 'Claims & limits' : item === 'evidence' ? `Evidence (${result.citations.length})` : `Hypotheses (${result.answer.hypotheses.length})`}</button>)}
          </div>
          {view === 'answer' && <div id="panel-answer" role="tabpanel" aria-labelledby="tab-answer" className="tab-panel">
            <div className="panel-title"><h4>Evidence-backed claims</h4><span>Only supported statements</span></div>
            {result.answer.claims.length === 0 && <p className="muted">No source-backed claim can be established from this evidence.</p>}
            <div className="claims-stack">{result.answer.claims.map((claim, index) => <article className="claim-card" key={`${index}-${claim.category}`}>
              <div className="claim-header"><span className="claim-index">{String(index + 1).padStart(2, '0')}</span>
                <span className="claim-category">{claim.category.replaceAll('_', ' ')}</span></div>
              <p>{claim.text}</p>{claim.excerpt && <blockquote className="claim-quote">{claim.excerpt}</blockquote>}
              <CitationLinks labels={claim.citations} onSelect={openCitation} />
            </article>)}</div>
            <div className="limitations-block"><div className="panel-title"><h4>Limitations</h4><span>What this answer cannot establish</span></div>
              {result.answer.limitations.length === 0 ? <p className="muted">No limitations were provided.</p> :
                <ul className="limitations">{result.answer.limitations.map((text, index) => <li key={index}>{text}</li>)}</ul>}</div>
          </div>}
          {view === 'evidence' && <div id="panel-evidence" role="tabpanel" aria-labelledby="tab-evidence" className="tab-panel">
            <div className="panel-title"><h4>Source and observation registry</h4><span>{result.citations.length} admitted items</span></div>
            <p className="evidence-intro">Only server-admitted redacted evidence is displayed. Git and runtime clues do not confirm execution.</p>
            {result.citations.length === 0 ? <p className="muted">No admissible evidence.</p> :
              result.citations.map((citation) => <EvidenceCard key={citation.label} item={citation} active={activeCitation === citation.label} />)}
          </div>}
          {view === 'hypotheses' && <div id="panel-hypotheses" role="tabpanel" aria-labelledby="tab-hypotheses" className="tab-panel">
            <div className="panel-title"><h4>Potential causes & missing proof</h4><span>Hypotheses, not verdicts</span></div>
            {result.answer.hypotheses.length === 0 ? <p className="muted">No supported hypotheses were returned.</p> :
              result.answer.hypotheses.map((hyp, index) => <article className="claim-card hypothesis-card" key={index}>
                <div className="claim-header"><span className="claim-index">H{index + 1}</span><span className="claim-category">{hyp.status.replaceAll('_', ' ')}</span></div>
                <p>{hyp.summary}</p><div className="hypothesis-evidence"><div><strong>Supporting</strong><CitationLinks labels={hyp.supporting_citations} onSelect={openCitation} /></div>
                  <div><strong>Contradicting</strong><CitationLinks labels={hyp.contradicting_citations} onSelect={openCitation} /></div></div>
                {hyp.missing_observations.length > 0 && <div className="missing-observations"><strong>Still needed</strong>
                  <ul>{hyp.missing_observations.map((missing, i) => <li key={i}>{missing}</li>)}</ul></div>}
              </article>)}
          </div>}
        </section>}
        <footer className="main-footer"><span className="footer-mark">TR / VERIFIED INPUTS</span><span>TraceRAG displays cited hypotheses, not a guaranteed causal verdict. No arbitrary file execution occurs in this interface.</span></footer>
      </main>
    </div>
  </div>;
}
