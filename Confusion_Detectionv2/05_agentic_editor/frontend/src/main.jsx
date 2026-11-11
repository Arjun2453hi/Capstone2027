import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { createPortal } from 'react-dom';
import { ArrowDownToLine, ArrowRight, Check, CheckCircle2, ChevronDown, ChevronRight, CircleAlert, Clock3, FileText, Layers3, LayoutDashboard, Loader2, Maximize2, Play, Search, ShieldCheck, SlidersHorizontal, Square, Terminal, X } from 'lucide-react';
import './styles.css';

let sessionToken = '';
async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, { ...options, headers: { 'Content-Type': 'application/json', 'X-Workspace-Token': sessionToken, ...options.headers } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail ? JSON.stringify(data.detail) : `Request failed (${response.status})`);
  return data;
}
function saveJson(name, data) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function openDeck(id) {
  document.cookie = id ? `deck=${encodeURIComponent(id)}; path=/; SameSite=Strict` : 'deck=; path=/; max-age=0; SameSite=Strict';
  location.reload();
}

const GAP = {
  complete_omission: { label: 'Missing', tone: 'danger', help: 'The deck never explains what students asked about.' },
  shallow_coverage: { label: 'Too thin', tone: 'warning', help: 'Mentioned, but not explained in enough depth.' },
  fragmented_context: { label: 'Scattered', tone: 'info', help: 'The explanation exists but is split across distant slides.' },
  covered: { label: 'Covered', tone: 'success', help: 'The deck already answers these questions.' },
  off_topic: { label: 'Off-topic', tone: 'neutral', help: 'The questions are about subjects this deck does not teach.' },
};
const OPERATION = { insert_slide_after: 'New slide', append_bullets: 'Added bullets', repair_slide: 'Layout repair' };
const NAV = [['review', Layers3, 'Review changes'], ['layouts', LayoutDashboard, 'Layout repairs'], ['evaluation', SlidersHorizontal, 'Evaluation'], ['activity', Clock3, 'Activity']];

function IconButton({ title, children, ...props }) { return <button className="icon-button" title={title} aria-label={title} {...props}>{children}</button>; }
function Badge({ children, tone = 'neutral' }) { return <span className={`badge ${tone}`}>{children}</span>; }
function GapBadge({ type }) { const g = GAP[type]; return g ? <Badge tone={g.tone}>{g.label}</Badge> : null; }
function Metric({ label, value, detail }) { return <div className="metric"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>; }
function percent(metric) { return metric?.value == null ? 'Not measured' : `${(metric.value * 100).toFixed(1)}%`; }
function fraction(metric) { return metric ? `${metric.numerator} of ${metric.denominator}` : 'Awaiting review'; }
function PageHeader({ title, description, children }) {
  return <div className="page-header"><div><h1>{title}</h1>{description && <p>{description}</p>}</div>{children && <div className="page-header-actions">{children}</div>}</div>;
}
function Empty({ icon: Icon, title, children }) {
  return <div className="empty-state">{Icon && <span className="empty-icon"><Icon size={20}/></span>}<h3>{title}</h3>{children}</div>;
}

function SlideImage({ src, title, fallback }) {
  const [failed, setFailed] = useState(false), [loaded, setLoaded] = useState(false);
  const [enlarged, setEnlarged] = useState(false);
  const closeButton = useRef(null), trigger = useRef(null);
  useEffect(() => { setFailed(false); setLoaded(false); }, [src]);
  useEffect(() => {
    if (!enlarged) return;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    closeButton.current?.focus();
    const keydown = event => {
      if (event.key === 'Escape') setEnlarged(false);
      if (event.key === 'Tab') { event.preventDefault(); closeButton.current?.focus(); }
    };
    window.addEventListener('keydown', keydown);
    return () => { document.body.style.overflow = oldOverflow; window.removeEventListener('keydown', keydown); trigger.current?.focus(); };
  }, [enlarged]);
  return <div className={`slide-image ${loaded ? 'loaded' : ''}`}>
    {!failed && !loaded && <div className="image-loading"><Loader2 size={18} className="spin"/>Rendering slide</div>}
    {failed ? <div className="slide-fallback">{fallback || <><FileText size={18}/><p>Preview unavailable. The slide text is in the tabs below.</p></>}</div> : <img src={src} alt={title} onLoad={() => setLoaded(true)} onError={() => setFailed(true)}/>}
    {!failed && loaded && <button className="slide-zoom" ref={trigger} aria-label={`Enlarge ${title}`} onClick={() => setEnlarged(true)}><Maximize2 size={13}/>Expand</button>}
    {enlarged && createPortal(<div className="preview-backdrop" role="presentation" onClick={event => { if (event.target === event.currentTarget) setEnlarged(false); }}><section className="preview-dialog" role="dialog" aria-modal="true" aria-label={title}><div><strong>{title}</strong><button ref={closeButton} className="icon-button" aria-label="Close slide preview" onClick={() => setEnlarged(false)}><X size={18}/></button></div><img src={src} alt={title}/></section></div>, document.body)}
  </div>;
}

function PipelineEmpty({ workspace }) {
  const s = workspace.pipeline || {};
  const steps = [['Slides parsed', s.parsed], ['Topics found', s.segmented], ['Questions added', s.questions], ['Questions mapped', s.mapped], ['Gap reports', s.reports > 0]];
  return <div className="card pipeline-empty">
    <h2>No gap reports for this deck yet</h2>
    <p>The editor works from the gap reports the pipeline writes. Run the remaining steps, then reload this page.</p>
    <ol className="steps">{steps.map(([label, done]) => <li key={label} className={done ? 'done' : ''}>{done ? <CheckCircle2 size={16}/> : <span className="step-dot"/>}{label}</li>)}</ol>
    <div className="command"><Terminal size={14}/><code>python run_pipeline.py run {workspace.deck_id}</code></div>
  </div>;
}

function App() {
  const [workspace, setWorkspace] = useState(null), [proposals, setProposals] = useState([]), [runs, setRuns] = useState([]);
  const [page, setPage] = useState('review'), [topicId, setTopicId] = useState(null), [topic, setTopic] = useState(null);
  const [selectedId, setSelectedId] = useState(null), [search, setSearch] = useState(''), [filter, setFilter] = useState('gaps');
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false), [exported, setExported] = useState(null);
  const [tab, setTab] = useState('questions'), [metrics, setMetrics] = useState(null), [issues, setIssues] = useState(null), [activeRun, setActiveRun] = useState(null);
  const fileInput = useRef(null);
  async function refresh() {
    const [w, p, r] = await Promise.all([api('/workspace'), api('/proposals'), api('/runs')]);
    sessionToken = w.token; setWorkspace(w); setProposals(p); setRuns(r);
    setTopicId(old => old ?? w.topics.find(t => t.eligible)?.id ?? w.topics[0]?.id ?? null);
  }
  useEffect(() => { refresh().catch(e => setError(e.message)); }, []);
  useEffect(() => {
    if (!workspace) return;
    const timer = setInterval(() => {
      Promise.all([api('/runs'), api('/proposals')]).then(([r, p]) => { setRuns(r); setProposals(p); }).catch(e => setError(e.message));
    }, 3000);
    return () => clearInterval(timer);
  }, [!!workspace]);
  useEffect(() => {
    if (topicId == null) return;
    const controller = new AbortController(); setTopic(null); setSelectedId(null);
    api(`/topics/${topicId}`, { signal: controller.signal }).then(setTopic).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, [topicId]);
  const proposalVersion = proposals.map(p => p.id).sort().join('|');
  useEffect(() => {
    if (page === 'evaluation') api('/evaluation').then(fresh => setMetrics(old => old?.snapshot_id === fresh.snapshot_id && old.human ? old : fresh)).catch(e => setError(e.message));
  }, [page, proposalVersion]);
  useEffect(() => {
    const shortcut = event => {
      if (event.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) {
        const input = document.querySelector('input[aria-label="Search topics"]');
        if (input) { event.preventDefault(); input.focus(); }
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, []);
  useEffect(() => { if (!notice) return; const id = setTimeout(() => setNotice(''), 5000); return () => clearTimeout(id); }, [notice]);
  async function act(fn) {
    setError(''); setBusy(true);
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function decide(proposal, decision) {
    await api(`/proposals/${proposal.id}/decision`, { method: 'POST', body: JSON.stringify({ decision }) });
    setProposals(old => old.map(p => p.id === proposal.id ? { ...p, decision } : p));
    setNotice(decision === 'approved' ? 'Change approved. It will be included in the next export.' : decision === 'rejected' ? 'Change set aside.' : 'Decision cleared.');
  }
  async function startRun(kind, target_id) {
    const run = await api('/runs', { method: 'POST', body: JSON.stringify({ kind, target_id }) });
    setRuns(old => [run, ...old]); setNotice('Run queued. Progress is shown in Activity.');
  }
  const approved = proposals.filter(p => p.decision === 'approved');
  const selectedTopic = workspace?.topics.find(t => t.id === topicId);
  const candidates = proposals.filter(p => p.topic_id === topicId && p.operation !== 'repair_slide');
  const current = candidates.find(p => p.id === selectedId) || candidates.find(p => p.accepted && p.decision !== 'rejected') || candidates[0];
  const gaps = workspace?.topics.filter(t => t.eligible).length || 0;
  const currentRun = runs.find(r => r.target_id === topicId && r.kind === 'gap' && ['queued', 'running'].includes(r.status));
  const sourceSlide = topic?.slides.find(s => s.id === current?.after_slide_id) || topic?.slides[0];
  const questionWord = workspace?.question_source === 'synthetic' ? 'synthetic questions' : 'student questions';
  const noLiveHint = 'Live generation needs GROQ_API_KEY (or GROQ_API_KEY_1, _2, …) in the server .env';

  if (!workspace) return <main className="boot">
    <div className="logo">M</div>
    {error ? <><h1>The workspace could not be opened</h1><p role="alert">{error}</p><div className="row"><button className="button primary" onClick={() => location.reload()}>Try again</button><button className="button secondary" onClick={() => openDeck(null)}>Open the default deck</button></div></>
      : <><h1>Loading workspace</h1><Loader2 className="spin muted"/></>}
  </main>;

  return <div className="app-shell">
    <aside className="sidebar">
      <button className="brand" aria-label="Margin home" onClick={() => setPage('review')}><span className="logo">M</span><span>Margin</span></button>
      <nav aria-label="Main navigation">{NAV.map(([key, Icon, label]) => <button key={key} className={`nav-link ${page === key ? 'active' : ''}`} onClick={() => setPage(key)} aria-label={label} aria-current={page === key ? 'page' : undefined}><Icon size={17}/><span>{label}</span></button>)}</nav>
      <div className="sidebar-note"><ShieldCheck size={15}/><span>Exports are written to a copy. The original deck is never modified.</span></div>
    </aside>
    <div className="app-body">
      <header className="topbar">
        <div className="deck-switch">
          {workspace.decks.length > 1 ? <label className="select-wrap"><span className="sr-only">Deck</span><select className="deck-picker" aria-label="Deck" value={workspace.deck_id} onChange={e => openDeck(e.target.value)}>{workspace.decks.map(d => <option key={d.id} value={d.id} disabled={!d.ready}>{d.title}{d.ready ? '' : ' — not processed'}</option>)}</select><ChevronDown size={15}/></label> : <h1 className="deck-title">{workspace.title}</h1>}
          <span className="deck-meta">{workspace.format === 'pdf' ? 'PDF' : 'PowerPoint'} · {workspace.slide_count} slides · {workspace.question_count} {questionWord}</span>
          {workspace.question_source === 'synthetic' && <Badge tone="warning">Synthetic questions</Badge>}
        </div>
        <div className="header-actions">
          <span className={`status ${workspace.live_available ? 'on' : ''}`} title={workspace.live_available ? `Suggestions are generated with ${workspace.model}` : noLiveHint}><span className="status-dot"/>{workspace.live_available ? workspace.model.replace(/^.*\//, '') : 'Read-only'}</span>
          <button className="button primary" disabled={busy || !approved.length} title={approved.length ? undefined : 'Approve at least one change to export'} onClick={() => act(async () => { const result = await api('/apply', { method: 'POST', body: JSON.stringify({ proposal_ids: approved.map(p => p.id) }) }); setExported(result); setNotice('Edited deck exported.'); await refresh(); })}><ArrowDownToLine size={15}/>{busy ? 'Working…' : 'Export deck'}<span className="count">{approved.length}</span></button>
        </div>
      </header>
      {error && <div role="alert" className="banner error"><CircleAlert size={16}/><span>{error}</span><IconButton title="Dismiss error" onClick={() => setError('')}><X size={15}/></IconButton></div>}
      {exported && <div className="banner success"><CheckCircle2 size={16}/><span>Edited deck exported.</span><a href={exported.download_url} download>{workspace.format === 'pdf' ? 'Download PDF' : 'Download PowerPoint'}</a><a href={exported.changes_url} download>Change log</a><IconButton title="Dismiss download" onClick={() => setExported(null)}><X size={14}/></IconButton></div>}
      {notice && <div role="status" className="toast"><Check size={15}/>{notice}</div>}

      {page === 'review' && (workspace.topics.length === 0 ? <main className="page"><PageHeader title="Review changes" description="Compare each suggested slide with the original, then approve the ones to export."/><PipelineEmpty workspace={workspace}/></main> : <div className="review-layout">
        <aside className="topic-panel">
          <div className="topic-panel-head"><h2>Topics</h2><span className="muted">{workspace.topics.length}</span></div>
          <label className="search"><Search size={15}/><input aria-label="Search topics" placeholder="Search topics" value={search} onChange={e => setSearch(e.target.value)}/><kbd>/</kbd></label>
          <div className="segmented" role="group" aria-label="Topic filter">{[['gaps', `With gaps · ${gaps}`], ['all', 'All topics']].map(([key, label]) => <button key={key} className={filter === key ? 'selected' : ''} aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>)}</div>
          <div className="topic-list">{workspace.topics.filter(t => (filter === 'all' || t.eligible) && `${t.title} ${t.id}`.toLowerCase().includes(search.toLowerCase())).map(t => {
            const done = proposals.some(p => p.topic_id === t.id && p.decision === 'approved');
            return <button className={`topic-item ${topicId === t.id ? 'selected' : ''}`} key={t.id} onClick={() => setTopicId(t.id)}>
              <span className="topic-title">{t.title}</span>
              <span className="topic-meta">{t.slide_ids.length ? `Slides ${t.slide_ids[0] + 1}–${t.slide_ids.at(-1) + 1}` : 'No slide range'} · {t.question_count} {t.question_count === 1 ? 'question' : 'questions'}</span>
              <span className="topic-tags"><GapBadge type={t.gap_type}/>{done && <Badge tone="success"><Check size={11}/>Approved</Badge>}</span>
            </button>;
          })}</div>
        </aside>
        <main className="page review-main">
          <PageHeader title="Review changes" description="Compare each suggested slide with the original, then approve the ones to export."/>
          <section className="card topic-summary">
            <div className="topic-summary-text">
              <div className="row"><GapBadge type={selectedTopic?.gap_type}/><span className="muted small">{GAP[selectedTopic?.gap_type]?.help}</span></div>
              <h2>{selectedTopic?.title}</h2>
              <div className="meta-row"><span>{selectedTopic?.slide_ids.length || 0} slides</span><span>{selectedTopic?.question_count} {questionWord}</span><span>Report confidence {Math.round((selectedTopic?.confidence || 0) * 100)}%</span></div>
            </div>
            <button className="button secondary" disabled={busy || !selectedTopic?.eligible || !workspace.live_available || !!currentRun} title={!workspace.live_available ? noLiveHint : !selectedTopic?.eligible ? 'This topic needs no change' : undefined} onClick={() => act(() => startRun('gap', topicId))}>{currentRun ? <Loader2 size={15} className="spin"/> : <Play size={14}/>}{currentRun ? 'Generating…' : 'Generate suggestion'}</button>
          </section>
          {currentRun && <div className="run-banner"><Loader2 className="spin" size={15}/><span>{currentRun.message}</span><button className="link" onClick={() => setPage('activity')}>View activity <ArrowRight size={13}/></button></div>}
          {!topic ? <div className="card loading-card"><Loader2 className="spin muted"/>Loading topic</div> : <>
            {!!candidates.length && <div className="proposal-switcher"><span className="muted small">Suggestions</span><div>{candidates.map((p, i) => <button key={p.id} className={current?.id === p.id ? 'selected' : ''} aria-label={`Suggestion ${i + 1}${p.accepted ? '' : ', blocked'}`} onClick={() => setSelectedId(p.id)}>{i + 1}{p.decision === 'approved' && <Check size={12}/>}<span className={`mini-dot ${p.accepted ? 'pass' : 'blocked'}`}/></button>)}</div></div>}
            {current && <div className={`approval-bar ${current.decision === 'approved' ? 'is-approved' : ''}`} aria-label="Change approval">
              <div className="approval-text">{current.decision === 'approved' ? <><CheckCircle2 size={18} className="success-text"/><div><strong>Approved</strong><small>Included in the next export.</small></div></> : <><ShieldCheck size={18}/><div><strong>{current.accepted ? current.decision === 'rejected' ? 'Set aside' : 'Pending your approval' : 'Blocked by checks'}</strong><small>{current.accepted ? 'Passed the automatic and fact checks. Nothing is applied until you approve it.' : 'This draft failed a check and cannot be approved.'}</small></div></>}</div>
              <div className="approval-actions"><button className="button ghost" disabled={busy} onClick={() => act(() => decide(current, current.decision === 'rejected' || current.decision === 'approved' ? 'pending' : 'rejected'))}>{current.decision !== 'pending' ? 'Clear decision' : 'Set aside'}</button><button className={`button ${current.decision === 'approved' ? 'secondary' : 'primary'}`} disabled={busy || !current.accepted || current.decision === 'approved'} onClick={() => act(() => decide(current, 'approved'))}><Check size={15}/>{current.decision === 'approved' ? 'Approved' : 'Approve change'}</button></div>
            </div>}
            {!!current?.problems.length && <div className="findings"><CircleAlert size={16}/><div><strong>Why approval is blocked</strong><ul>{current.problems.map((p, i) => <li key={i}>{p}</li>)}</ul></div></div>}
            <div className="comparison-grid">
              <section className="slide-panel"><div className="panel-head"><strong>Original</strong><span className="muted small">Slide {sourceSlide ? sourceSlide.id + 1 : '—'}</span></div>{sourceSlide ? <SlideImage src={`/api/slides/${sourceSlide.id}/image`} title={`Original slide ${sourceSlide.id + 1}`}/> : <div className="slide-image"><div className="slide-fallback"><p>No slides in this topic.</p></div></div>}<div className="panel-foot">{sourceSlide?.title || 'Source deck'}</div></section>
              <section className={`slide-panel ${current?.decision === 'approved' ? 'is-approved' : ''}`}><div className="panel-head"><strong>{current ? OPERATION[current.operation] : 'Suggestion'}</strong>{current && <Badge tone={current.accepted ? 'success' : 'danger'}>{current.accepted ? 'Checks passed' : 'Checks failed'}</Badge>}</div>{current ? <SlideImage src={`/api/proposals/${current.id}/image`} title={current.title} fallback={<div className="text-slide"><h3>{current.title}</h3><ul>{current.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul></div>}/> : <div className="slide-image"><Empty icon={Layers3} title={selectedTopic?.eligible ? 'No suggestion yet' : 'No change needed'}><p>{selectedTopic?.eligible ? (workspace.live_available ? 'Generate a suggestion to compare it with the original slide.' : noLiveHint + '.') : 'The report found no gap to close in this topic.'}</p></Empty></div>}<div className="panel-foot">{current ? `${current.operation === 'append_bullets' ? `Adds to slide ${current.after_slide_id + 1}` : `Inserted after slide ${current.after_slide_id + 1}`} · ${current.revisions} ${current.revisions === 1 ? 'revision' : 'revisions'}` : '—'}</div></section>
            </div>
            <section className="card evidence"><div className="tabs" role="tablist">{[['questions', 'Student questions', topic.questions.length], ['report', 'Gap report'], ['source', 'Slide text'], ['content', 'Suggested text']].map(([key, label, count]) => <button role="tab" aria-selected={tab === key} className={tab === key ? 'selected' : ''} key={key} onClick={() => setTab(key)}>{label}{count != null && <span className="tab-count">{count}</span>}</button>)}</div>
              <div className="tab-content" role="tabpanel">
                {tab === 'questions' && <><p className="hint">Questions mapped to this topic. Questions the suggestion claims to answer are marked.</p><ol className="question-list">{topic.questions.map((q, i) => <li key={i} className={current?.addresses_questions.includes(q) ? 'addressed' : ''}><span>{q}</span>{current?.addresses_questions.includes(q) && <Badge tone="success">Addressed</Badge>}</li>)}</ol></>}
                {tab === 'report' && <div className="prose">{topic.report.report_text}</div>}
                {tab === 'source' && <div className="prose">{sourceSlide?.text || 'No extractable text on this slide.'}</div>}
                {tab === 'content' && (current ? <div className="prose"><h3>{current.title}</h3><ul>{current.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul></div> : <p className="hint">No suggestion selected.</p>)}
              </div>
            </section>
          </>}
        </main>
      </div>)}

      {page === 'evaluation' && <main className="page">
        <PageHeader title="Evaluation" description="Automatic results for the saved suggestions, and a form for independent human review. Makes no model calls."><button className="button secondary" onClick={() => act(async () => saveJson('editor-review.json', await api('/evaluation/template')))}><ArrowDownToLine size={15}/>Review form</button></PageHeader>
        {metrics ? <>
          <div className="stats-grid"><Metric label="Passed all checks" value={percent(metrics.automatic.recorded_pass_rate)} detail={`${fraction(metrics.automatic.recorded_pass_rate)} saved suggestions`}/><Metric label="Revised after review" value={percent(metrics.automatic.revised_proposal_rate)} detail={fraction(metrics.automatic.revised_proposal_rate)}/><Metric label="Usable (human review)" value={percent(metrics.human?.usable_rate_among_reviewed)} detail={metrics.human ? `${fraction(metrics.human.usable_rate_among_reviewed)} fully reviewed` : 'Needs an independent review'}/><Metric label="Question coverage change" value={metrics.human?.coverage.improvement_percentage_points == null ? 'Not measured' : `${metrics.human.coverage.improvement_percentage_points.toFixed(1)} pp`} detail={metrics.human ? `${fraction(metrics.human.coverage.paired_review_completion)} questions reviewed` : 'Needs before/after review'}/></div>
          <section className="card"><div className="card-head"><div><h2>Human review</h2><p>Judge each suggestion on the actual original and edited decks, including blocked ones.</p></div></div>
            <div className="criteria">{[['Correct', 'Every statement checks out against a trusted reference.'], ['Fulfils its purpose', 'Resolves the confusion, or keeps the meaning in a repair.'], ['Readable', 'Clear wording and a clean rendered slide.'], ['Useful', 'Adds missing explanation without duplicating the deck.']].map(([title, text]) => <div key={title}><h3>{title}</h3><p>{text}</p></div>)}</div>
            <div className="card-foot"><span><strong>{metrics.human?.reviewer ? `Reviewed by ${metrics.human.reviewer}` : 'No review loaded'}</strong><small>Download the form, set each criterion to true or false, and upload it. Leave unreviewed fields as null.</small></span><input ref={fileInput} type="file" accept=".json,application/json" hidden onChange={e => { const file = e.target.files?.[0]; if (file) act(async () => { if (file.size > 1_000_000) throw new Error('Review form exceeds 1 MB'); const form = JSON.parse(await file.text()); setMetrics(await api('/evaluation', { method: 'POST', body: JSON.stringify(form) })); setNotice('Review loaded. Scores updated.'); }); e.target.value = ''; }}/><button className="button primary" onClick={() => fileInput.current.click()}>Upload review</button></div>
          </section>
          {metrics.human && <section className="card"><div className="card-head"><h2>Review completeness & outcomes</h2></div><div className="score-table">{[['Suggestions reviewed', metrics.human.edit_review_completion], ['Confirmed usable, of all suggestions', metrics.human.confirmed_usable_yield_all_proposals], ['Usable, of reviewed accepted suggestions', metrics.human.usable_rate_among_reviewed_accepted], ['Questions answered by the original deck', metrics.human.coverage.before], ['Questions answered by the edited deck', metrics.human.coverage.after], ['Questions reviewed before and after', metrics.human.coverage.paired_review_completion]].map(([name, m]) => <div key={name}><span>{name}</span><small>{fraction(m)}</small><strong>{percent(m)}</strong></div>)}</div></section>}
          <section className="card"><div className="card-head"><h2>Reading these numbers</h2><button className="button ghost" onClick={() => saveJson('editor-metrics.json', metrics)}><ArrowDownToLine size={14}/>Export metrics</button></div><ul className="notes">{metrics.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></section>
        </> : <div className="card loading-card"><Loader2 className="spin muted"/>Loading evaluation</div>}
      </main>}

      {page === 'layouts' && <main className="page">
        <PageHeader title="Layout repairs" description="Find slides with overlapping or hidden text. A repair is planned only after you confirm the slide is broken.">
          <button className="button primary" disabled={busy || !workspace.capabilities?.layout_repair} title={workspace.capabilities?.layout_repair ? undefined : 'Layout repair needs the deck as .pptx (and its PDF)'} onClick={() => act(async () => setIssues(await api('/layout-issues')))}>{busy ? <Loader2 size={15} className="spin"/> : <Search size={15}/>}Scan layouts</button>
        </PageHeader>
        {!workspace.capabilities?.layout_repair ? <div className="card"><Empty icon={LayoutDashboard} title="Not available for PDF decks"><p>Layout repair rebuilds slides from their shapes, which needs the deck as .pptx. Add the .pptx to this deck to enable it.</p></Empty></div>
          : issues == null ? <div className="card"><Empty icon={LayoutDashboard} title="No scan yet"><p>The scan reads the deck file directly and makes no model calls.</p></Empty></div>
          : <div className="layout-grid">{issues.map(issue => { const repairs = proposals.filter(p => p.operation === 'repair_slide' && p.after_slide_id === issue.slide_id); const running = runs.some(r => r.kind === 'repair' && r.target_id === issue.slide_id && ['running', 'queued'].includes(r.status)); return <section className="card layout-card" key={issue.slide_id}><div className="card-head"><h2>Slide {issue.slide_id + 1}</h2><Badge tone="warning">Check</Badge></div><SlideImage src={`/api/slides/${issue.slide_id}/image`} title={`Slide ${issue.slide_id + 1}`}/><ul className="notes">{issue.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul><div className="card-foot"><button className="button secondary" disabled={busy || !workspace.live_available || running} title={workspace.live_available ? undefined : noLiveHint} onClick={() => act(() => startRun('repair', issue.slide_id))}>{running ? <Loader2 size={14} className="spin"/> : <Play size={14}/>}Confirm & plan repair</button></div>{repairs.map(p => <div className="repair-result" key={p.id}><div className="row"><strong>{p.title}</strong><Badge tone={p.accepted ? 'success' : 'danger'}>{p.accepted ? 'Checks passed' : 'Checks failed'}</Badge></div><SlideImage src={`/api/proposals/${p.id}/image`} title={`Repair of slide ${issue.slide_id + 1}`}/>{!!p.problems.length && <p className="danger-text">{p.problems.join('; ')}</p>}<button className="button primary" disabled={busy || !p.accepted} onClick={() => act(() => decide(p, p.decision === 'approved' ? 'pending' : 'approved'))}><Check size={14}/>{p.decision === 'approved' ? 'Clear approval' : 'Approve repair'}</button></div>)}</section>; })}{!issues.length && <div className="card"><Empty icon={CheckCircle2} title="No layout problems found"/></div>}</div>}
      </main>}

      {page === 'activity' && <main className="page">
        <PageHeader title="Activity" description="Generation and export runs for this deck. Runs can be inspected step by step and cancelled.">
          <Badge>{runs.filter(r => r.status === 'running').length} running</Badge>
        </PageHeader>
        <section className="card"><div className="card-head"><h2>Runs</h2><span className="muted small">Stored locally</span></div>
          {runs.length ? runs.map(run => <div className="run-row" key={run.id}>
            <span className={`run-icon ${run.status}`}>{run.status === 'running' ? <Loader2 className="spin" size={15}/> : run.status === 'completed' ? <Check size={15}/> : run.status === 'failed' ? <CircleAlert size={15}/> : <Clock3 size={15}/>}</span>
            <div className="run-copy"><strong>{run.kind === 'gap' ? `Topic ${run.target_id + 1} · Suggestion` : run.kind === 'repair' ? `Slide ${run.target_id + 1} · Layout repair` : 'Export'}</strong><span>{run.message}</span><small>{new Date(run.created_at * 1000).toLocaleString()} · {run.id.slice(0, 8)}</small></div>
            <Badge tone={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'danger' : 'neutral'}>{run.status}</Badge>
            <button className="button ghost" onClick={() => act(async () => setActiveRun(await api(`/runs/${run.id}`)))}>Details <ChevronRight size={14}/></button>
            {['running', 'queued'].includes(run.status) && <IconButton title="Cancel run" disabled={busy} onClick={() => act(async () => { const data = await api(`/runs/${run.id}/cancel`, { method: 'POST' }); setNotice(data.message); })}><Square size={14}/></IconButton>}
          </div>) : <Empty icon={Clock3} title="No runs yet"><p>Suggestion, repair and export runs will be listed here.</p></Empty>}
        </section>
        {activeRun && <section className="card"><div className="card-head"><h2>Run {activeRun.id.slice(0, 8)}</h2><IconButton title="Close run details" onClick={() => setActiveRun(null)}><X size={16}/></IconButton></div>{activeRun.telemetry && <div className="stats-grid compact"><Metric label="Model requests" value={activeRun.telemetry.calls}/><Metric label="Elapsed" value={`${activeRun.telemetry.elapsed_seconds}s`}/><Metric label="Tokens used" value={activeRun.telemetry.responses_with_usage ? activeRun.telemetry.actual_tokens.toLocaleString() : 'Unavailable'}/><Metric label="Tokens reserved (estimate)" value={activeRun.telemetry.estimated_reserved_tokens.toLocaleString()}/></div>}<div className="event-list">{activeRun.events.map((event, i) => <div key={i}><span>{new Date(event.at * 1000).toLocaleTimeString()}</span><strong>{event.event.replaceAll('_', ' ')}</strong><code>{Object.entries(event).filter(([k]) => !['at', 'event'].includes(k)).map(([k, v]) => `${k}: ${v}`).join(' · ')}</code></div>)}</div></section>}
        <details className="card policy-details"><summary>Execution limits <ChevronDown size={15}/></summary><div className="policy-grid">{[['Provider', 'Groq'], ['Model', workspace.model], ['Concurrent live runs', '1'], ['Queued or active tasks', '4 maximum'], ['Model calls per run', workspace.policy.max_calls], ['Run deadline', `${workspace.policy.deadline_seconds}s`], ['Request timeout', `${workspace.policy.request_timeout_seconds}s`], ['Prompt + output budget', `${workspace.policy.max_request_tokens.toLocaleString()} tokens`], ['Retries per call', workspace.policy.max_attempts - 1], ['Evidence', 'Relevant slide excerpts, rebuilt per task'], ['Durable state', 'Local SQLite checkpoints and approvals'], ['History', '100 recent runs · 150 events per run']].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div></details>
      </main>}
    </div>
  </div>;
}

class ErrorBoundary extends React.Component {
  state = { error: false };
  static getDerivedStateFromError() { return { error: true }; }
  render() { return this.state.error ? <main className="boot"><div className="logo">M</div><h1>Something went wrong</h1><p>Your approvals are saved. Reload to continue.</p><button className="button primary" onClick={() => location.reload()}>Reload</button></main> : this.props.children; }
}
createRoot(document.getElementById('root')).render(<ErrorBoundary><App/></ErrorBoundary>);
