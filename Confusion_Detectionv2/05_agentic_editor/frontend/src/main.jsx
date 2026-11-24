import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { createPortal } from 'react-dom';
import { ArrowDownToLine, ArrowRight, Check, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, CircleAlert, Clock3, FileText, Layers3, Loader2, Maximize2, Play, Search, ShieldCheck, SlidersHorizontal, Square, Terminal, X } from 'lucide-react';
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
const OPERATION = { insert_slide_after: 'New slide', append_bullets: 'Added bullets' };
const NAV = [['review', Layers3, 'Review changes'], ['evaluation', SlidersHorizontal, 'Evaluation'], ['activity', Clock3, 'Activity']];

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

function RunDetails({ runId, onClose }) {
  const [run, setRun] = useState(null), [error, setError] = useState('');
  const heading = useRef(null);
  useEffect(() => {
    heading.current?.focus();
    const controller = new AbortController();
    let timer;
    async function load() {
      try {
        const fresh = await api(`/runs/${runId}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setRun(fresh); setError('');
        if (['running', 'queued'].includes(fresh.status)) timer = setTimeout(load, 2000);
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(e.message); timer = setTimeout(load, 3000);
      }
    }
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [runId]);
  const telemetry = run?.telemetry;
  return <section className="run-details" aria-label="Run details">
    <div className="card-head"><h2 ref={heading} tabIndex={-1}>Run {runId.slice(0, 8)}</h2><IconButton title="Close run details" onClick={onClose}><X size={16}/></IconButton></div>
    {error && <p className="run-detail-status" role="alert">{error} Retrying…</p>}
    {!run ? <p className="run-detail-status"><Loader2 className="spin" size={15}/>Loading run details…</p> : <>
      <div className="run-detail-status" role="status"><Badge tone={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'danger' : 'info'}>{run.status}</Badge><span>{run.message || 'Waiting to start'}</span>{['running', 'queued'].includes(run.status) && <small>Updates live</small>}</div>
      {telemetry && <div className="stats-grid compact"><Metric label="Model requests" value={telemetry.calls ?? 0}/><Metric label="Elapsed" value={`${telemetry.elapsed_seconds ?? 0}s`}/><Metric label="Tokens used" value={telemetry.responses_with_usage ? (telemetry.actual_tokens ?? 0).toLocaleString() : 'Unavailable'}/><Metric label="Tokens reserved (estimate)" value={(telemetry.estimated_reserved_tokens ?? 0).toLocaleString()}/></div>}
      <div className="event-list">{run.events?.length ? run.events.map((event, i) => <div key={i}><span>{new Date(event.at * 1000).toLocaleTimeString()}</span><strong>{event.event.replaceAll('_', ' ')}</strong><code>{Object.entries(event).filter(([k]) => !['at', 'event'].includes(k)).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ')}</code></div>) : <p className="hint">No events yet. Progress will appear here as the run continues.</p>}</div>
    </>}
  </section>;
}

function QuestionChanges({ topic, candidates, busy, liveAvailable, eligible, running, onGenerate, onDecide, onSelect, onAssess, assessing }) {
  const [filter, setFilter] = useState('all');
  const questions = [...new Set([...(topic.questions || []), ...(topic.report.question_verdicts || []).map(v => v.question), ...candidates.flatMap(p => p.addresses_questions || [])])];
  const rows = questions.map(question => {
    const assessment = topic.report.question_verdicts?.find(v => v.question === question);
    const verdict = assessment?.verdict;
    const edits = candidates.filter(p => p.addresses_questions?.includes(question));
    const needsChange = verdict && verdict !== 'unknown' ? ['complete_omission', 'shallow_coverage', 'fragmented_context'].includes(verdict) : edits.length > 0;
    return { question, verdict, assessment, edits, needsChange };
  });
  const needing = rows.filter(row => row.needsChange);
  const visible = filter === 'changes' ? needing : rows;
  function renderEdit(edit) {
    const adding = edit.operation === 'insert_slide_after';
    const location = adding ? `New slide after original slide ${edit.after_slide_id + 1}` : `Changes on original slide ${edit.after_slide_id + 1}`;
    return <section className={`question-change ${edit.decision === 'approved' ? 'approved-change' : ''}`} key={edit.id} aria-label={`Change for question: ${edit.title || location}`}>
      <div className="question-change-header"><div><strong>{location}</strong><p>{edit.title || 'Additional explanation'}</p></div><Badge tone={edit.decision === 'approved' ? 'success' : edit.decision === 'rejected' ? 'neutral' : 'warning'}>{edit.decision === 'approved' ? 'Approved for export' : edit.decision === 'rejected' ? 'Set aside' : 'Needs approval'}</Badge></div>
      <div className="question-change-body"><SlideImage src={`/api/proposals/${edit.id}/image`} title={edit.title || location} fallback={<div className="text-slide"><h3>{edit.title}</h3><ul>{edit.bullets.map((bullet, i) => <li key={i}>{bullet}</li>)}</ul></div>}/><div className="question-change-text"><h4>What changes</h4><ul>{edit.bullets.map((bullet, i) => <li key={i}>{bullet}</li>)}</ul>{edit.problems?.length > 0 && <details><summary>Review notes ({edit.problems.length})</summary><ul>{edit.problems.map((problem, i) => <li key={i}>{problem}</li>)}</ul></details>}</div></div>
      <div className="card-foot"><button className="button secondary" onClick={() => onSelect(edit)}>View in topic slide order <ArrowRight size={14}/></button><div className="row">{edit.decision !== 'pending' && <button className="button ghost" disabled={busy} onClick={() => onDecide(edit, 'pending')}>Clear decision</button>}<button className="button primary" disabled={busy || edit.decision === 'approved'} onClick={() => onDecide(edit, 'approved')}><Check size={14}/>{edit.decision === 'approved' ? 'Approved' : 'Approve this change'}</button></div></div>
    </section>;
  }
  return <section className="question-change-map" aria-label="Question to slide changes">
    <div className="card question-map-heading"><div className="card-head"><div><h2>All topic questions</h2><p>Every question stays visible. Check whether it needs work, then review the explanation and new slide below.</p></div><Badge>{needing.length} need changes</Badge></div><div className="question-filters" role="group" aria-label="Question filter"><button className="button secondary" disabled={busy || running || assessing || !liveAvailable || !topic.slides.length || !questions.length} onClick={onAssess}>{assessing ? <Loader2 className="spin" size={14}/> : <ShieldCheck size={14}/>}Assess every question</button><button className={`button ${filter === 'changes' ? 'primary' : 'secondary'}`} aria-pressed={filter === 'changes'} onClick={() => setFilter('changes')}>Needs changes ({needing.length})</button><button className={`button ${filter === 'all' ? 'primary' : 'secondary'}`} aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>All questions ({rows.length})</button></div></div>
    {visible.map(({ question, verdict, assessment, edits, needsChange }) => {
      const preferred = [...edits].sort((a, b) => (a.decision === 'approved' ? 0 : a.decision === 'pending' ? 1 : 2) - (b.decision === 'approved' ? 0 : b.decision === 'pending' ? 1 : 2));
      return <article className="card question-change-card" key={question} aria-label={question}><div className="question-card-heading"><h3>{question}</h3><Badge tone={needsChange ? 'warning' : verdict === 'covered' ? 'success' : 'neutral'}>{needsChange ? 'Change needed: Yes' : verdict === 'covered' ? 'Change needed: No' : verdict === 'off_topic' ? 'Change needed: No — outside scope' : 'Change needed: Not assessed'}</Badge></div>{verdict && <p className="question-verdict">{assessment?.reason || `Gap report: ${GAP[verdict]?.label || verdict}`}</p>}
        {preferred.length && needsChange ? <>{renderEdit(preferred[0])}{preferred.length > 1 && <details className="change-alternatives"><summary>Other proposed changes / versions ({preferred.length - 1})</summary>{preferred.slice(1).map(renderEdit)}</details>}</> : <div className="question-no-change"><p>{needsChange ? 'The gap report identifies a problem, but no slide change has been generated for this question yet.' : verdict === 'covered' ? 'The gap report says the existing slides cover this question. No change is required.' : verdict === 'off_topic' ? 'No slide change is proposed for this question.' : 'The saved report does not contain an individual verdict for this question.'}</p>{needsChange && <button className="button secondary" disabled={busy || running || !liveAvailable || !eligible} onClick={() => onGenerate(question)}>{running ? <Loader2 className="spin" size={14}/> : <Play size={14}/>}Generate change for this question</button>}</div>}
      </article>;
    })}
    {!visible.length && <div className="card"><Empty icon={CheckCircle2} title="No individually identified changes yet"><p>Open all questions to see the saved assessments, or generate suggestions for this topic.</p></Empty></div>}
  </section>;
}

function neededInDraft(edit, topic) {
  if (edit.decision === 'approved') return true;
  return (edit.addresses_questions || []).some(question => {
    const verdict = topic?.report.question_verdicts?.find(row => row.question === question)?.verdict;
    return !verdict || ['unknown', 'complete_omission', 'shallow_coverage', 'fragmented_context'].includes(verdict);
  });
}

function draftEdits(proposals) {
  const eligible = proposals.filter(edit => edit.operation !== 'repair_slide' && edit.decision !== 'rejected');
  const ordered = [...eligible].sort((a, b) => Number(b.decision === 'approved') - Number(a.decision === 'approved') || Number(b.accepted) - Number(a.accepted));
  const selected = new Map();
  const coveredQuestions = new Set();
  for (const edit of ordered) {
    const questionKeys = (edit.addresses_questions || []).map(q => `${edit.topic_id}:${q}`);
    if (questionKeys.length && questionKeys.every(q => coveredQuestions.has(q))) continue;
    questionKeys.forEach(q => coveredQuestions.add(q));
    const key = `${edit.after_slide_id}:${edit.operation}:${edit.operation === 'append_bullets' ? '' : [...(edit.addresses_questions || [])].sort().join('|')}`;
    if (!selected.has(key)) selected.set(key, edit);
  }
  return [...selected.values()];
}

function UpdatedDeck({ topic, candidates, onSelect, onExport, canExport, busy }) {
  const [index, setIndex] = useState(0);
  const included = draftEdits(candidates.filter(edit => neededInDraft(edit, topic)));
  const pages = topic.slides.flatMap(slide => {
    const additions = included.filter(edit => edit.after_slide_id === slide.id);
    const modified = additions.find(edit => edit.operation === 'append_bullets');
    return [{ key: `slide-${slide.id}`, source: slide, edit: modified, label: modified ? `Updated original slide ${slide.id + 1}` : `Original slide ${slide.id + 1}` }, ...additions.filter(edit => edit.operation === 'insert_slide_after').map(edit => ({ key: edit.id, edit, label: `New slide after original slide ${slide.id + 1}` }))];
  });
  const activeIndex = Math.min(index, Math.max(0, pages.length - 1));
  const current = pages[activeIndex];
  return <section className="card updated-deck" aria-label="Updated slide deck"><div className="card-head"><div><h2>New slide deck</h2><p>All {topic.slides.length} original topic slides, with proposed new slides inserted in order. This draft includes approved changes and pending proposals.</p></div><button className="button primary" disabled={!canExport || busy} onClick={onExport}><ArrowDownToLine size={14}/>Download new deck (draft)</button></div>
    <div className="deck-page-controls"><IconButton title="Previous deck slide" disabled={activeIndex === 0} onClick={() => setIndex(activeIndex - 1)}><ChevronLeft size={18}/></IconButton><strong>Slide {pages.length ? activeIndex + 1 : 0} of {pages.length}</strong><IconButton title="Next deck slide" disabled={activeIndex >= pages.length - 1} onClick={() => setIndex(activeIndex + 1)}><ChevronRight size={18}/></IconButton><span>{current?.label}</span>{current?.edit && <Badge tone={current.edit.decision === 'approved' ? 'success' : 'warning'}>{current.edit.decision === 'approved' ? 'Approved change' : current.edit.accepted ? 'Draft change' : 'Draft — review notes'}</Badge>}</div>
    {current && <SlideImage src={current.edit ? `/api/proposals/${current.edit.id}/image` : `/api/slides/${current.source.id}/image`} title={current.label} fallback={<div className="text-slide"><h3>{current.edit?.title || current.source?.title}</h3><p>{current.edit?.bullets?.join(' ') || current.source?.text}</p></div>}/>}
    {current?.edit?.problems?.length > 0 && <details className="deck-review-notes"><summary>Review notes for this draft slide ({current.edit.problems.length})</summary><ul>{current.edit.problems.map((note, i) => <li key={i}>{note}</li>)}</ul></details>}
    <div className="deck-thumbnails" role="group" aria-label="New deck slides">{pages.map((page, i) => <button key={page.key} className={i === activeIndex ? 'selected' : ''} aria-pressed={i === activeIndex} onClick={() => { setIndex(i); if (page.edit) onSelect(page.edit); }}><span>{i + 1}. {page.label}</span>{page.edit && <Badge tone={page.edit.decision === 'approved' ? 'success' : 'warning'}>{page.edit.operation === 'insert_slide_after' ? 'New' : 'Updated'} · {page.edit.decision === 'approved' ? 'approved' : 'draft'}</Badge>}</button>)}</div>
  </section>;
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
  const [selectedSlideKey, setSelectedSlideKey] = useState(null), [search, setSearch] = useState(''), [filter, setFilter] = useState('all');
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false), [exported, setExported] = useState(null);
  const [tab, setTab] = useState('report'), [metrics, setMetrics] = useState(null), [activeRunId, setActiveRunId] = useState(null);
  const fileInput = useRef(null);
  const seenAssessments = useRef(new Set());
  const [showAllSlides, setShowAllSlides] = useState(true);
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
    const controller = new AbortController(); setTopic(null); setSelectedSlideKey(null);
    api(`/topics/${topicId}`, { signal: controller.signal }).then(setTopic).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, [topicId]);
  const completedAssessment = runs.find(run => run.kind === 'assessment' && run.target_id === topicId && run.status === 'completed');
  useEffect(() => {
    if (!completedAssessment || seenAssessments.current.has(completedAssessment.id)) return;
    seenAssessments.current.add(completedAssessment.id);
    Promise.all([api(`/topics/${topicId}`), api('/workspace')]).then(([fresh, w]) => { setTopic(fresh); setWorkspace(w); }).catch(e => setError(e.message));
  }, [completedAssessment?.id, topicId]);
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
  async function startRun(kind, target_id, question = null) {
    const run = await api('/runs', { method: 'POST', body: JSON.stringify({ kind, target_id, ...(question ? { question } : {}) }) });
    setRuns(old => [run, ...old]); setNotice('Run queued. Progress is shown in Activity.');
  }
  const approved = proposals.filter(p => p.decision === 'approved' && p.operation !== 'repair_slide');
  const draft = draftEdits([...approved, ...proposals.filter(edit => edit.topic_id === topicId && neededInDraft(edit, topic))]);
  async function exportDraft() {
    const result = await api('/draft', { method: 'POST', body: JSON.stringify({ proposal_ids: draft.map(p => p.id) }) });
    setExported(result); setNotice('Your new draft slide deck is ready.');
    const link = document.createElement('a'); link.href = result.download_url; link.download = ''; link.click();
  }
  async function exportDeck() {
    const result = await api('/apply', { method: 'POST', body: JSON.stringify({ proposal_ids: approved.map(p => p.id) }) });
    setExported(result); setNotice('Your new slide deck is ready to download.'); await refresh();
    const link = document.createElement('a'); link.href = result.download_url; link.download = ''; link.click();
  }

  const selectedTopic = workspace?.topics.find(t => t.id === topicId);
  const candidates = proposals.filter(p => p.topic_id === topicId && p.operation !== 'repair_slide');
  const slideEntries = (topic?.slides || []).flatMap(slide => [
    { key: `source-${slide.id}`, slide, label: `Slide ${slide.id + 1}` },
    ...candidates.filter(p => p.after_slide_id === slide.id).map(proposal => ({ key: `proposal-${proposal.id}`, proposal, label: `${OPERATION[proposal.operation]} ${proposal.operation === 'append_bullets' ? 'on' : 'after'} slide ${slide.id + 1}` })),
  ]);
  candidates.filter(p => !topic?.slides.some(s => s.id === p.after_slide_id)).forEach(proposal => slideEntries.push({ key: `proposal-${proposal.id}`, proposal, label: `${OPERATION[proposal.operation]} after slide ${proposal.after_slide_id + 1}` }));
  const selectedEntry = slideEntries.find(e => e.key === selectedSlideKey) || slideEntries[0];
  const current = selectedEntry?.proposal;
  const slideIndex = slideEntries.indexOf(selectedEntry);
  const gaps = workspace?.topics.filter(t => t.eligible).length || 0;
  const currentRun = runs.find(r => r.target_id === topicId && ['gap', 'assessment'].includes(r.kind) && ['queued', 'running'].includes(r.status));
  const sourceSlide = selectedEntry?.slide || topic?.slides.find(s => s.id === current?.after_slide_id);
  const visibleRuns = runs.filter(r => r.kind !== 'repair');
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
          <button className="button primary" disabled={busy || !approved.length} title={approved.length ? undefined : 'Approve at least one change to export'} onClick={() => act(exportDeck)}><ArrowDownToLine size={15}/>{busy ? 'Working…' : 'Download approved deck'}<span className="count">{approved.length}</span></button>
        </div>
      </header>
      {error && <div role="alert" className="banner error"><CircleAlert size={16}/><span>{error}</span><IconButton title="Dismiss error" onClick={() => setError('')}><X size={15}/></IconButton></div>}
      {exported && <div className="banner success"><CheckCircle2 size={16}/><span>{exported.draft ? 'New draft deck ready.' : 'Approved deck ready.'}</span><a href={exported.download_url} download>{workspace.format === 'pdf' ? 'Download PDF' : 'Download PowerPoint'}</a><a href={exported.changes_url} download>Change log</a><IconButton title="Dismiss download" onClick={() => setExported(null)}><X size={14}/></IconButton></div>}
      {notice && <div role="status" className="toast"><Check size={15}/>{notice}</div>}

      {page === 'review' && (workspace.topics.length === 0 ? <main className="page"><PageHeader title="Review changes" description="Compare each suggested slide with the original, then approve the ones to export."/><PipelineEmpty workspace={workspace}/></main> : <div className="review-layout">
        <aside className="topic-panel">
          <div className="topic-panel-head"><h2>Topics</h2><span className="muted">{workspace.topics.length}</span></div>
          <label className="search"><Search size={15}/><input aria-label="Search topics" placeholder="Search topics" value={search} onChange={e => setSearch(e.target.value)}/><kbd>/</kbd></label>
          <div className="segmented" role="group" aria-label="Topic filter">{[['gaps', `With gaps · ${gaps}`], ['all', 'All topics']].map(([key, label]) => <button key={key} className={filter === key ? 'selected' : ''} aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>)}</div>
          <div className="topic-list">{workspace.topics.filter(t => (filter === 'all' || t.eligible) && `${t.title} ${t.id}`.toLowerCase().includes(search.toLowerCase())).map(t => {
            const topicProposals = proposals.filter(p => p.topic_id === t.id && p.operation !== 'repair_slide');
            const approvedCount = topicProposals.filter(p => p.decision === 'approved').length;
            return <button className={`topic-item ${topicId === t.id ? 'selected' : ''}`} key={t.id} onClick={() => setTopicId(t.id)}>
              <span className="topic-title">{t.title}</span>
              <span className="topic-meta">{t.slide_ids.length ? `Slides ${t.slide_ids[0] + 1}–${t.slide_ids.at(-1) + 1}` : 'No slide range'} · {t.question_count} {t.question_count === 1 ? 'question' : 'questions'}</span>
              <span className="topic-tags"><GapBadge type={t.gap_type}/>{approvedCount > 0 && <Badge tone="success"><Check size={11}/>{approvedCount}/{topicProposals.length} changes approved</Badge>}</span>
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
          {currentRun && <div className="run-banner"><Loader2 className="spin" size={15}/><span>{currentRun.message}</span><button className="link" onClick={() => { setActiveRunId(currentRun.id); setPage('activity'); }}>View activity <ArrowRight size={13}/></button></div>}
          {!topic ? <div className="card loading-card"><Loader2 className="spin muted"/>Loading topic</div> : <>
            <UpdatedDeck key={`deck-${topicId}`} topic={topic} candidates={candidates} canExport={draft.length > 0} busy={busy} onExport={() => act(exportDraft)} onSelect={edit => setSelectedSlideKey(`proposal-${edit.id}`)}/>
            <QuestionChanges key={topicId} topic={topic} candidates={candidates} busy={busy} liveAvailable={workspace.live_available} eligible={selectedTopic?.eligible} running={!!currentRun} onAssess={() => act(() => startRun('assessment', topicId))} assessing={currentRun?.kind === 'assessment'} onGenerate={question => act(() => startRun('gap', topicId, question))} onDecide={(edit, decision) => act(() => decide(edit, decision))} onSelect={edit => { setSelectedSlideKey(`proposal-${edit.id}`); requestAnimationFrame(() => document.getElementById('topic-slide-order')?.scrollIntoView({ behavior: 'smooth', block: 'start' })); }}/>
            <section id="topic-slide-order" className="card slide-browser" aria-label="Topic slides">
              <div className="card-head"><div><h2>Topic slide order</h2><p>{topic.slides.length} original slides · {candidates.filter(p => p.operation === 'insert_slide_after').length} new slide proposals · {candidates.filter(p => p.decision === 'approved').length} approved changes</p></div><div className="row"><button className="button ghost" aria-pressed={showAllSlides} onClick={() => setShowAllSlides(value => !value)}>{showAllSlides ? 'Compact view' : 'Show all slides'}</button><IconButton title="Previous slide" disabled={slideIndex <= 0} onClick={() => setSelectedSlideKey(slideEntries[slideIndex - 1].key)}><ChevronLeft size={18}/></IconButton><span className="small muted">{slideIndex + 1} / {slideEntries.length}</span><IconButton title="Next slide" disabled={slideIndex < 0 || slideIndex >= slideEntries.length - 1} onClick={() => setSelectedSlideKey(slideEntries[slideIndex + 1].key)}><ChevronRight size={18}/></IconButton></div></div>
              <div className={`slide-strip ${showAllSlides ? 'show-all' : ''}`} role="group" aria-label="Select a topic slide">{slideEntries.map((entry, index) => {
                const questionCount = entry.proposal ? new Set(entry.proposal.addresses_questions || []).size : (topic.question_links || []).filter(q => q.slide_id === entry.slide.id).length;
                return <button key={entry.key} className={`slide-choice ${entry.proposal ? `proposed ${entry.proposal.decision === 'approved' ? 'approved' : ''}` : ''} ${selectedEntry?.key === entry.key ? 'selected' : ''}`} aria-pressed={selectedEntry?.key === entry.key} onClick={() => setSelectedSlideKey(entry.key)}><span>{entry.proposal ? <Layers3 size={15}/> : <FileText size={15}/>} {entry.label}</span><small>{entry.proposal?.title || entry.slide?.title || 'Untitled slide'}</small><span className="slide-position">Review position {index + 1} · {questionCount} {questionCount === 1 ? 'question' : 'questions'}</span>{entry.proposal && <Badge tone={entry.proposal.decision === 'approved' ? 'success' : 'neutral'}>{entry.proposal.decision === 'approved' ? 'Approved for export' : entry.proposal.decision === 'rejected' ? 'Set aside' : 'Proposed'}</Badge>}</button>;
              })}</div>

            </section>
            {current && <div className={`approval-bar ${current.decision === 'approved' ? 'is-approved' : ''}`} aria-label="Change approval">
              <div className="approval-text">{current.decision === 'approved' ? <><CheckCircle2 size={18} className="success-text"/><div><strong>Approved</strong><small>Included in the next export.</small></div></> : <><ShieldCheck size={18}/><div><strong>{current.decision === 'rejected' ? 'Set aside' : 'Pending your approval'}</strong><small>{current.accepted ? 'Passed the automatic and fact checks. Nothing is applied until you approve it.' : 'Review the notes below. You can approve this draft for export.'}</small></div></>}</div>
              <div className="approval-actions"><button className="button ghost" disabled={busy} onClick={() => act(() => decide(current, current.decision === 'rejected' || current.decision === 'approved' ? 'pending' : 'rejected'))}>{current.decision !== 'pending' ? 'Clear decision' : 'Set aside'}</button><button className={`button ${current.decision === 'approved' ? 'secondary' : 'primary'}`} disabled={busy || current.decision === 'approved'} onClick={() => act(() => decide(current, 'approved'))}><Check size={15}/>{current.decision === 'approved' ? 'Approved' : 'Approve change'}</button></div>
            </div>}
            {!!current?.problems.length && <div className="findings"><CircleAlert size={16}/><div><strong>Review notes (advisory)</strong><ul>{current.problems.map((p, i) => <li key={i}>{p}</li>)}</ul></div></div>}
            <div className={`comparison-grid ${current ? '' : 'source-only'}`}>
              <section className="slide-panel"><div className="panel-head"><strong>Original</strong><span className="muted small">Slide {sourceSlide ? sourceSlide.id + 1 : '—'}</span></div>{sourceSlide ? <SlideImage src={`/api/slides/${sourceSlide.id}/image`} title={`Original slide ${sourceSlide.id + 1}`}/> : <div className="slide-image"><div className="slide-fallback"><p>No slides in this topic.</p></div></div>}<div className="panel-foot">{sourceSlide?.title || 'Source deck'}</div></section>
              {current && <section className={`slide-panel ${current.decision === 'approved' ? 'is-approved' : ''}`}><div className="panel-head"><strong>{OPERATION[current.operation]}</strong><Badge tone={current.accepted ? 'success' : 'info'}>{current.accepted ? 'Checks passed' : 'Review notes'}</Badge></div><SlideImage src={`/api/proposals/${current.id}/image`} title={current.title} fallback={<div className="text-slide"><h3>{current.title}</h3><ul>{current.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul></div>}/><div className="panel-foot">{current.operation === 'append_bullets' ? `Adds to slide ${current.after_slide_id + 1}` : `Inserted after slide ${current.after_slide_id + 1}`} · {current.revisions} {current.revisions === 1 ? 'revision' : 'revisions'}</div></section>}

            </div>
            <section className="card evidence"><div className="tabs" role="tablist">{[['report', 'Gap report'], ['source', 'Slide text'], ['content', 'Suggested text']].map(([key, label, count]) => <button role="tab" aria-selected={tab === key} className={tab === key ? 'selected' : ''} key={key} onClick={() => setTab(key)}>{label}{count != null && <span className="tab-count">{count}</span>}</button>)}</div>
              <div className="tab-content" role="tabpanel">
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
            <div className="criteria">{[['Correct', 'Every statement checks out against a trusted reference.'], ['Fulfils its purpose', 'Resolves the confusion identified in the gap report.'], ['Readable', 'Clear wording and a clean rendered slide.'], ['Useful', 'Adds missing explanation without duplicating the deck.']].map(([title, text]) => <div key={title}><h3>{title}</h3><p>{text}</p></div>)}</div>
            <div className="card-foot"><span><strong>{metrics.human?.reviewer ? `Reviewed by ${metrics.human.reviewer}` : 'No review loaded'}</strong><small>Download the form, set each criterion to true or false, and upload it. Leave unreviewed fields as null.</small></span><input ref={fileInput} type="file" accept=".json,application/json" hidden onChange={e => { const file = e.target.files?.[0]; if (file) act(async () => { if (file.size > 1_000_000) throw new Error('Review form exceeds 1 MB'); const form = JSON.parse(await file.text()); setMetrics(await api('/evaluation', { method: 'POST', body: JSON.stringify(form) })); setNotice('Review loaded. Scores updated.'); }); e.target.value = ''; }}/><button className="button primary" onClick={() => fileInput.current.click()}>Upload review</button></div>
          </section>
          {metrics.human && <section className="card"><div className="card-head"><h2>Review completeness & outcomes</h2></div><div className="score-table">{[['Suggestions reviewed', metrics.human.edit_review_completion], ['Confirmed usable, of all suggestions', metrics.human.confirmed_usable_yield_all_proposals], ['Usable, of reviewed accepted suggestions', metrics.human.usable_rate_among_reviewed_accepted], ['Questions answered by the original deck', metrics.human.coverage.before], ['Questions answered by the edited deck', metrics.human.coverage.after], ['Questions reviewed before and after', metrics.human.coverage.paired_review_completion]].map(([name, m]) => <div key={name}><span>{name}</span><small>{fraction(m)}</small><strong>{percent(m)}</strong></div>)}</div></section>}
          <section className="card"><div className="card-head"><h2>Reading these numbers</h2><button className="button ghost" onClick={() => saveJson('editor-metrics.json', metrics)}><ArrowDownToLine size={14}/>Export metrics</button></div><ul className="notes">{metrics.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></section>
        </> : <div className="card loading-card"><Loader2 className="spin muted"/>Loading evaluation</div>}
      </main>}

      {page === 'activity' && <main className="page">
        <PageHeader title="Activity" description="Generation and export runs for this deck. Runs can be inspected step by step and cancelled.">
          <Badge>{visibleRuns.filter(r => r.status === 'running').length} running</Badge>
        </PageHeader>
        <section className="card"><div className="card-head"><h2>Runs</h2><span className="muted small">Stored locally</span></div>
          {visibleRuns.length ? visibleRuns.map(run => <React.Fragment key={run.id}><div className="run-row">
            <span className={`run-icon ${run.status}`}>{run.status === 'running' ? <Loader2 className="spin" size={15}/> : run.status === 'completed' ? <Check size={15}/> : run.status === 'failed' ? <CircleAlert size={15}/> : <Clock3 size={15}/>}</span>
            <div className="run-copy"><strong>{run.kind === 'gap' ? `Topic ${run.target_id + 1} · Suggestion` : run.kind === 'assessment' ? `Topic ${run.target_id + 1} · Question assessment` : 'Export'}</strong><span>{run.message}</span><small>{new Date(run.created_at * 1000).toLocaleString()} · {run.id.slice(0, 8)}</small></div>
            <Badge tone={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'danger' : 'neutral'}>{run.status}</Badge>
            <button className="button ghost" aria-expanded={activeRunId === run.id} onClick={() => setActiveRunId(old => old === run.id ? null : run.id)}>Details <ChevronRight size={14}/></button>
            {['running', 'queued'].includes(run.status) && <IconButton title="Cancel run" disabled={busy} onClick={() => act(async () => { const data = await api(`/runs/${run.id}/cancel`, { method: 'POST' }); setNotice(data.message); })}><Square size={14}/></IconButton>}
          </div>{activeRunId === run.id && <RunDetails key={run.id} runId={run.id} onClose={() => setActiveRunId(null)}/>}</React.Fragment>) : <Empty icon={Clock3} title="No runs yet"><p>Suggestion and export runs will be listed here.</p></Empty>}
        </section>
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
