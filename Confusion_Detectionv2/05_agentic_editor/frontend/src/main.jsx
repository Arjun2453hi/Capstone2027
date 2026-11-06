import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { createPortal } from 'react-dom';
import { ArrowDownToLine, ArrowLeft, ArrowRight, Check, CheckCheck, ChevronDown, ChevronRight, Circle, CircleAlert, Clock3, FileText, Layers3, LayoutDashboard, Loader2, PanelLeftClose, Play, Search, ShieldCheck, SlidersHorizontal, Sparkles, Square, X } from 'lucide-react';
import './styles.css';
import './contrast.css';

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
const labels = { shallow_coverage: 'Needs more depth', complete_omission: 'Missing explanation', fragmented_context: 'Scattered context', covered: 'Already covered' };
const opLabels = { insert_slide_after: 'New slide', append_bullets: 'Additional bullets', repair_slide: 'Layout repair' };
function IconButton({ title, children, ...props }) { return <button className="icon-button" title={title} aria-label={title} {...props}>{children}</button>; }
function Badge({ children, tone = '' }) { return <span className={`badge ${tone}`}>{children}</span>; }
function Metric({ label, value, detail }) { return <div className="metric"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>; }
function percent(metric) { return metric?.value == null ? 'Not measured' : `${(metric.value * 100).toFixed(1)}%`; }
function fraction(metric) { return metric ? `${metric.numerator} of ${metric.denominator}` : 'Awaiting review'; }
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
    {!failed && !loaded && <div className="image-loading"><Loader2 size={20} className="spin"/> Loading slide</div>}
    {failed ? <div className="slide-fallback">{fallback || <><FileText/><p>Preview unavailable. Use the slide text below.</p></>}</div> : <img src={src} alt={title} onLoad={() => setLoaded(true)} onError={() => setFailed(true)}/>}
    {!failed && loaded && <button className="slide-zoom" ref={trigger} aria-label={`Enlarge ${title}`} onClick={() => setEnlarged(true)}>Expand <ArrowRight size={12}/></button>}
    {enlarged && createPortal(<div className="preview-backdrop" role="presentation" onClick={event => { if (event.target === event.currentTarget) setEnlarged(false); }}><section className="preview-dialog" role="dialog" aria-modal="true" aria-label={title}><div><strong>{title}</strong><button ref={closeButton} className="icon-button" aria-label="Close slide preview" onClick={() => setEnlarged(false)}><X size={21}/></button></div><img src={src} alt={title}/></section></div>, document.body)}
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
    setTopicId(old => old ?? w.topics.find(t => t.eligible)?.id ?? w.topics[0]?.id);
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
      if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
        const input = document.querySelector('input[aria-label="Search topics"]');
        if (input) { event.preventDefault(); input.focus(); }
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, []);
  useEffect(() => { if (!notice) return; const id = setTimeout(() => setNotice(''), 6000); return () => clearTimeout(id); }, [notice]);
  async function act(fn) {
    setError(''); setBusy(true);
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function decide(proposal, decision) {
    await api(`/proposals/${proposal.id}/decision`, { method: 'POST', body: JSON.stringify({ decision }) });
    setProposals(old => old.map(p => p.id === proposal.id ? { ...p, decision } : p));
    setNotice(decision === 'approved' ? 'Change approved. Ready to export.' : decision === 'rejected' ? 'Change set aside.' : 'Approval cleared.');
  }
  async function startRun(kind, target_id) {
    const run = await api('/runs', { method: 'POST', body: JSON.stringify({ kind, target_id }) });
    setRuns(old => [run, ...old]); setNotice('Run queued. Follow its progress in Activity.');
  }
  const approved = proposals.filter(p => p.decision === 'approved');
  const selectedTopic = workspace?.topics.find(t => t.id === topicId);
  const candidates = proposals.filter(p => p.topic_id === topicId && p.operation !== 'repair_slide');
  const current = candidates.find(p => p.id === selectedId) || candidates.find(p => p.accepted && p.decision !== 'rejected') || candidates[0];
  const gaps = workspace?.topics.filter(t => t.eligible).length || 0;
  const currentRun = runs.find(r => r.target_id === topicId && r.kind === 'gap' && ['queued', 'running'].includes(r.status));
  const sourceSlide = topic?.slides.find(s => s.id === current?.after_slide_id) || topic?.slides[0];

  if (!workspace) return <main className="boot"><div className="brand-mark">m.</div><h1>Opening your workspace</h1>{error ? <><p role="alert">{error}</p><button className="button primary" onClick={() => location.reload()}>Try again</button></> : <Loader2 className="spin"/>}</main>;

  return <div className="app-shell">
    <aside className="rail">
      <button className="brand-mark" aria-label="Margin home" onClick={() => setPage('review')}>m.</button>
      <nav aria-label="Main navigation">{[['review', Layers3, 'Review changes'], ['layouts', LayoutDashboard, 'Layout repairs'], ['evaluation', SlidersHorizontal, 'Evaluation'], ['activity', Clock3, 'Activity']].map(([key, Icon, label]) => <button key={key} className={`rail-link ${page === key ? 'active' : ''}`} onClick={() => setPage(key)} title={label} aria-label={label} aria-current={page === key ? 'page' : undefined}><Icon size={21}/><span>{label.split(' ')[0]}</span></button>)}</nav>
      <div className="rail-bottom" title="Local workspace"><span className="online-dot"/><span>Local</span></div>
    </aside>
    <div className="app-body">
      <header className="topbar"><div className="breadcrumbs"><span className="wordmark">Margin</span><span className="crumb-divider">/</span><span>Cloud Computing</span><ChevronRight size={13}/><span className="muted">Unit 02</span></div><div className="header-actions"><Badge><span className="online-dot"/> {workspace.live_available ? 'Groq connected' : 'Saved workspace'}</Badge><button className="button primary" disabled={busy || !approved.length} onClick={() => act(async () => { const result = await api('/apply', { method: 'POST', body: JSON.stringify({ proposal_ids: approved.map(p => p.id) }) }); setExported(result); setNotice('Your edited deck is ready.'); await refresh(); })}><ArrowDownToLine size={15}/>{busy ? 'Working…' : 'Export deck'}<span className="button-count">{approved.length}</span></button>{!approved.length && <span className="export-hint">Approve a change to enable export</span>}</div></header>
      {error && <div role="alert" className="alert error"><CircleAlert size={17}/><span>{error}</span><IconButton title="Dismiss error" onClick={() => setError('')}><X size={15}/></IconButton></div>}
      {notice && <div role="status" className="toast"><Check size={16}/>{notice}</div>}
      {exported && <div className="export-banner"><CheckCheck size={18}/><span>Edited deck ready</span><a href={exported.download_url} download>Download PowerPoint <ArrowDownToLine size={14}/></a><a href={exported.changes_url} download>Change log</a><IconButton title="Dismiss download" onClick={() => setExported(null)}><X size={14}/></IconButton></div>}

      {page === 'review' && <div className="review-layout">
        <aside className="topic-sidebar"><div className="sidebar-heading"><span className="eyebrow">LECTURE WORKSPACE</span><h2>Topics <span>{workspace.topics.length}</span></h2><p>{workspace.slide_count} slides · {workspace.question_count} student questions</p></div>
          <label className="search"><Search size={15}/><input aria-label="Search topics" placeholder="Find a topic…" value={search} onChange={e => setSearch(e.target.value)}/><kbd>/</kbd></label>
          <div className="segmented">{[['gaps', `Gaps ${gaps}`], ['all', 'All topics']].map(([key, label]) => <button key={key} className={filter === key ? 'selected' : ''} onClick={() => setFilter(key)}>{label}</button>)}</div>
          <div className="topic-list">{workspace.topics.filter(t => (filter === 'all' || t.eligible) && `${t.title} ${t.id}`.toLowerCase().includes(search.toLowerCase())).map(t => {
            const done = proposals.some(p => p.topic_id === t.id && p.decision === 'approved');
            return <button className={`topic-item ${topicId === t.id ? 'selected' : ''}`} key={t.id} onClick={() => setTopicId(t.id)}><span className="topic-number">{String(t.id + 1).padStart(2, '0')}</span><span className="topic-content"><strong>{t.title}</strong><small>{t.question_count} questions <span>·</span> {t.slide_ids.length ? `Slides ${t.slide_ids[0] + 1}–${t.slide_ids.at(-1) + 1}` : 'Outside this deck'}</small></span>{done ? <Check size={15} className="green"/> : <span className={`topic-dot ${t.eligible ? '' : 'quiet'}`}/>}</button>;
          })}</div><div className="sidebar-footer"><ShieldCheck size={16}/><span>Changes stay in a copy.<br/><strong>Your original is preserved.</strong></span></div>
        </aside>
        <main className="review-main"><div className="page-heading"><div><div className="eyebrow">REVIEW & REFINE <span className="eyebrow-rule"/></div><h1>Make room for understanding.</h1><p>Turn the questions students asked into explanations they can use.</p></div><span className="page-index">{String((topicId ?? 0) + 1).padStart(2, '0')} <span>/ {workspace.topics.length}</span></span></div>
          <section className="topic-overview"><div><Badge tone="warm">{labels[selectedTopic?.gap_type]}</Badge><h2>{selectedTopic?.title}</h2><div className="inline-meta"><span><FileText size={13}/> {selectedTopic?.slide_ids.length} source slides</span><span>{selectedTopic?.question_count} student questions</span><span>{Math.round((selectedTopic?.confidence || 0) * 100)}% report confidence</span></div></div><button className="button secondary" disabled={busy || !selectedTopic?.eligible || !workspace.live_available || !!currentRun} title={!workspace.live_available ? 'Set GROQ_API_KEY in the server .env to generate live suggestions' : undefined} onClick={() => act(() => startRun('gap', topicId))}>{currentRun ? <Loader2 size={15} className="spin"/> : <Play size={14}/>} {currentRun ? 'Investigating…' : 'Generate new suggestions'}</button></section>
          {currentRun && <div className="run-banner"><Loader2 className="spin" size={16}/><span>{currentRun.message}</span><button onClick={() => setPage('activity')}>View activity <ArrowRight size={14}/></button></div>}
          {!topic ? <div className="empty-state"><Loader2 className="spin"/>Loading source evidence…</div> : <>
            {!!candidates.length && <div className="proposal-switcher"><span>{candidates.length} proposed {candidates.length === 1 ? 'change' : 'changes'}</span><div>{candidates.map((p, i) => <button key={p.id} className={current?.id === p.id ? 'selected' : ''} onClick={() => setSelectedId(p.id)}>{i + 1}{p.decision === 'approved' && <Check size={12}/>}<span className={`mini-dot ${p.accepted ? 'pass' : 'blocked'}`}/></button>)}</div><small>Each change needs your approval</small></div>}
            {current && <div className="approval-bar" aria-label="Change approval"><div>{current.decision === 'approved' ? <><span className="approval-icon"><Check size={18}/></span><div><strong>Approved for your deck</strong><small>This change will be included in your next export.</small></div></> : <><ShieldCheck size={20}/><div><strong>{current.accepted ? current.decision === 'rejected' ? 'Change set aside' : 'Pending your approval' : 'Approval blocked by failed checks'}</strong><small>{current.accepted ? 'Only clicking Approve change approves this suggestion. Export then creates a separate deck.' : 'Correct this draft and run the checks again before approving.'}</small></div></>}</div><div className="approval-actions"><button className="button ghost" disabled={busy} onClick={() => act(() => decide(current, current.decision === 'rejected' || current.decision === 'approved' ? 'pending' : 'rejected'))}>{current.decision !== 'pending' ? 'Clear decision' : 'Set aside'}</button><button className={`button ${current.decision === 'approved' ? 'approved-button' : 'primary'}`} disabled={busy || !current.accepted || current.decision === 'approved'} onClick={() => act(() => decide(current, 'approved'))}><Check size={16}/>{current.decision === 'approved' ? 'Approved' : 'Approve change'}</button></div></div>}
            {!!current?.problems.length && <div className="findings"><CircleAlert size={17}/><div><strong>Why approval is blocked</strong><ul>{current.problems.map((p, i) => <li key={i}>{p}</li>)}</ul></div></div>}
            <div className="comparison-grid"><section className="slide-panel"><div className="panel-label"><span>01 <strong>Original slide</strong></span><Badge>Slide {sourceSlide ? sourceSlide.id + 1 : '—'}</Badge></div>{sourceSlide ? <SlideImage src={`/api/slides/${sourceSlide.id}/image`} title={`Original slide ${sourceSlide.id + 1}`}/> : <div className="empty-state">No slide evidence for this topic.</div>}<div className="slide-panel-footer"><FileText size={14}/><span>{sourceSlide?.title || 'Source deck'}</span><span className="muted">Original</span></div></section>
              <section className={`slide-panel proposed ${current?.decision === 'approved' ? 'is-approved' : ''}`}><div className="panel-label"><span>02 <strong>{current ? opLabels[current.operation] : 'Suggested change'}</strong></span>{current && <Badge tone={current.accepted ? 'green' : 'red'}>{current.accepted ? <ShieldCheck size={12}/> : <CircleAlert size={12}/>} {current.accepted ? 'Checks passed · approval required' : 'Checks failed'}</Badge>}</div>{current ? <SlideImage src={`/api/proposals/${current.id}/image`} title={current.title} fallback={<div className="text-slide"><h3>{current.title}</h3><ul>{current.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul></div>}/> : <div className="empty-state proposal-empty"><Layers3 size={28}/><h3>{selectedTopic?.eligible ? 'A clearer explanation starts here.' : 'No change needed.'}</h3><p>{selectedTopic?.eligible ? 'Generate a suggestion for this topic, then compare it with the original.' : 'Explore the report and questions below.'}</p>{!workspace.live_available && <small>Live generation needs GROQ_API_KEY in the server .env.</small>}</div>}<div className="slide-panel-footer"><span>{current ? current.operation === 'append_bullets' ? `Adds to slide ${current.after_slide_id + 1}` : `Follows slide ${current.after_slide_id + 1}` : 'Waiting for a suggestion'}</span>{current && <span className="muted">{current.revisions} {current.revisions === 1 ? 'revision' : 'revisions'}</span>}</div></section></div>
            <section className="evidence-panel"><div className="tabs" role="tablist">{[['questions', 'Student questions', topic.questions.length], ['report', 'Gap report'], ['source', 'Slide text'], ['content', 'Suggested text']].map(([key, label, count]) => <button role="tab" aria-selected={tab === key} className={tab === key ? 'selected' : ''} key={key} onClick={() => setTab(key)}>{label}{count != null && <span>{count}</span>}</button>)}</div><div className="tab-content" role="tabpanel">{tab === 'questions' && <><p className="section-note">The questions behind this topic. Highlighted questions are claimed by this suggestion; usefulness still needs review.</p><div className="question-list">{topic.questions.map((q, i) => <div key={i} className={current?.addresses_questions.includes(q) ? 'addressed' : ''}><span className="question-index">{String(i + 1).padStart(2, '0')}</span><span>{q}</span>{current?.addresses_questions.includes(q) && <Badge tone="green">Cited</Badge>}</div>)}</div></>}{tab === 'report' && <div className="prose">{topic.report.report_text}</div>}{tab === 'source' && <div className="prose">{sourceSlide?.text || 'No extractable text.'}</div>}{tab === 'content' && (current ? <div className="prose"><h3>{current.title}</h3><ul>{current.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul></div> : <p className="section-note">No suggestion selected.</p>)}</div></section>
          </>}
          <footer className="workspace-footer"><span>Margin · Lecture workspace</span><span>Every explanation deserves a second look.</span></footer>
        </main>
      </div>}

      {page === 'evaluation' && <main className="wide-main"><div className="page-heading"><div><div className="eyebrow">MEASURE THE DIFFERENCE</div><h1>Evidence, before confidence.</h1><p>Track what passed the checks, then measure what actually helped.</p></div><button className="button secondary" onClick={() => act(async () => saveJson('editor-review.json', await api('/evaluation/template')))}><ArrowDownToLine size={15}/>Review form</button></div>
        {metrics ? <><div className="stats-grid"><Metric label="Recorded pass rate" value={percent(metrics.automatic.recorded_pass_rate)} detail={`${fraction(metrics.automatic.recorded_pass_rate)} saved proposals`}/><Metric label="Proposals revised" value={percent(metrics.automatic.revised_proposal_rate)} detail={fraction(metrics.automatic.revised_proposal_rate)}/><Metric label="Human usability" value={percent(metrics.human?.usable_rate_among_reviewed)} detail={metrics.human ? `${fraction(metrics.human.usable_rate_among_reviewed)} fully reviewed` : 'Independent review required'}/><Metric label="Coverage improvement" value={metrics.human?.coverage.improvement_percentage_points == null ? 'Not measured' : `${metrics.human.coverage.improvement_percentage_points.toFixed(1)} pp`} detail={metrics.human ? `${fraction(metrics.human.coverage.paired_review_completion)} questions reviewed` : 'Paired before/after review required'}/></div>
        <section className="surface review-instructions"><div className="section-heading"><div><h2>Make the human review count.</h2><p>Use the actual original and edited decks. Assess all proposals, including blocked ones.</p></div><ShieldCheck size={25}/></div><div className="rubric-grid">{[['01', 'Technically correct', 'Verify the explanation against trusted references.'], ['02', 'Fulfills its purpose', 'Resolves the confusion or preserves meaning in a repair.'], ['03', 'Easy to read', 'Check both the wording and rendered slide layout.'], ['04', 'A useful change', 'Adds missing explanation or repairs a broken layout.']].map(([n, title, text]) => <div key={n}><span>{n}</span><h3>{title}</h3><p>{text}</p></div>)}</div><div className="review-upload"><span><strong>{metrics.human?.reviewer ? `Reviewed by ${metrics.human.reviewer}` : 'Your independent scores belong here.'}</strong><small>Download the form, fill true/false labels, then upload it. Unreviewed fields stay null.</small></span><input ref={fileInput} type="file" accept=".json,application/json" hidden onChange={e => { const file = e.target.files?.[0]; if (file) act(async () => { if (file.size > 1_000_000) throw new Error('Review form exceeds 1 MB'); const form = JSON.parse(await file.text()); setMetrics(await api('/evaluation', { method: 'POST', body: JSON.stringify(form) })); setNotice('Human review loaded. Scores updated.'); }); e.target.value = ''; }}/><button className="button primary" onClick={() => fileInput.current.click()}>Upload completed review <ArrowRight size={15}/></button></div></section>
        {metrics.human && <section className="surface"><div className="section-heading"><h2>Review completeness & outcomes</h2></div><div className="score-table">{[['Edit review completion', metrics.human.edit_review_completion], ['Confirmed usable across all proposals', metrics.human.confirmed_usable_yield_all_proposals], ['Usable among reviewed accepted edits', metrics.human.usable_rate_among_reviewed_accepted], ['Original deck question coverage', metrics.human.coverage.before], ['Edited deck question coverage', metrics.human.coverage.after], ['Paired question review completion', metrics.human.coverage.paired_review_completion]].map(([name, m]) => <div key={name}><span>{name}</span><small>{fraction(m)}</small><strong>{percent(m)}</strong></div>)}</div></section>}
        <section className="surface"><div className="section-heading"><h2>How to read these scores</h2><button className="button ghost" onClick={() => saveJson('editor-metrics.json', metrics)}><ArrowDownToLine size={14}/>Export metrics</button></div><ul className="limitations">{metrics.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul><p className="section-note">Human review uploads are evaluated in this browser session. Keep the downloaded JSON as your review record.</p></section></> : <div className="empty-state"><Loader2 className="spin"/>Loading evaluations…</div>}
      </main>}

      {page === 'layouts' && <main className="wide-main"><div className="page-heading"><div><div className="eyebrow">RESTORE THE READING EXPERIENCE</div><h1>Let the content breathe.</h1><p>Find overlapping text and obscured content. Confirm a slide before requesting a repair.</p></div><button className="button primary" disabled={busy} onClick={() => act(async () => setIssues(await api('/layout-issues')))}>{busy ? <Loader2 size={15} className="spin"/> : <Search size={15}/>}Scan layouts</button></div>{issues == null ? <div className="surface empty-state"><LayoutDashboard size={32}/><h2>A closer look at the deck.</h2><p>Run a layout scan to find slides worth checking. It uses the source deck and no model calls.</p></div> : <div className="layout-grid">{issues.map(issue => { const repairs = proposals.filter(p => p.operation === 'repair_slide' && p.after_slide_id === issue.slide_id); const running = runs.some(r => r.kind === 'repair' && r.target_id === issue.slide_id && ['running', 'queued'].includes(r.status)); return <section className="surface layout-card" key={issue.slide_id}><div className="section-heading"><h2>Slide {issue.slide_id + 1}</h2><Badge>Needs a look</Badge></div><SlideImage src={`/api/slides/${issue.slide_id}/image`} title={`Slide ${issue.slide_id + 1}`}/><ul className="layout-reasons">{issue.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul><button className="button secondary" disabled={busy || !workspace.live_available || running} onClick={() => act(() => startRun('repair', issue.slide_id))}>{running ? <Loader2 size={14} className="spin"/> : <Play size={14}/>}Confirm & plan repair</button>{repairs.map(p => <div className="repair-result" key={p.id}><strong>{p.title}</strong><Badge tone={p.accepted ? 'green' : 'red'}>{p.accepted ? 'Checks passed · approval required' : 'Checks failed'}</Badge><SlideImage src={`/api/proposals/${p.id}/image`} title={`Repair of slide ${issue.slide_id + 1}`}/><ul>{p.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul>{!!p.problems.length && <p className="red-text">{p.problems.join('; ')}</p>}<button className="button primary" disabled={busy || !p.accepted} onClick={() => act(() => decide(p, p.decision === 'approved' ? 'pending' : 'approved'))}><Check size={14}/>{p.decision === 'approved' ? 'Clear approval' : 'Approve repair'}</button></div>)}</section>; })}{!issues.length && <div className="empty-state">No candidate layout issues found.</div>}</div>}</main>}

      {page === 'activity' && <main className="wide-main"><div className="page-heading"><div><div className="eyebrow">A RECORD OF THE WORK</div><h1>Nothing behind the curtain.</h1><p>Follow each run, inspect its checks, and stop work when you need to.</p></div><Badge><span className="online-dot"/>{runs.filter(r => r.status === 'running').length} running</Badge></div><div className="surface"><div className="section-heading"><h2>Run history</h2><span className="muted">Stored locally</span></div>{runs.length ? runs.map(run => <div className="run-row" key={run.id}><div className={`run-status-icon ${run.status}`}>{run.status === 'running' ? <Loader2 className="spin" size={17}/> : run.status === 'completed' ? <Check size={17}/> : run.status === 'failed' ? <CircleAlert size={17}/> : <Clock3 size={17}/>}</div><div className="run-copy"><strong>{run.kind === 'gap' ? `Topic ${run.target_id + 1} · New explanations` : run.kind === 'repair' ? `Slide ${run.target_id + 1} · Layout repair` : 'Export approved changes'}</strong><span>{run.message}</span><small>{new Date(run.created_at * 1000).toLocaleString()} · {run.id.slice(0, 8)}</small></div><Badge tone={run.status === 'completed' ? 'green' : run.status === 'failed' ? 'red' : ''}>{run.status}</Badge><button className="button ghost" onClick={() => act(async () => setActiveRun(await api(`/runs/${run.id}`)))}>Details <ChevronRight size={14}/></button>{['running', 'queued'].includes(run.status) && <IconButton title="Cancel run" disabled={busy} onClick={() => act(async () => { const data = await api(`/runs/${run.id}/cancel`, { method: 'POST' }); setNotice(data.message); })}><Square size={15}/></IconButton>}</div>) : <div className="empty-state"><Clock3 size={30}/><h3>A clean slate.</h3><p>Your live generation and export runs will appear here.</p></div>}</div>
        <details className="surface policy-details"><summary>Execution limits & memory policy <ChevronDown size={16}/></summary><div className="policy-grid">{[['Provider', 'Groq'], ['Concurrent live runs', '1'], ['Queued / active tasks', '4 maximum'], ['Model calls per run', workspace.policy.max_calls], ['Run deadline', `${workspace.policy.deadline_seconds}s`], ['Request timeout', `${workspace.policy.request_timeout_seconds}s`], ['Prompt + output estimate', `${workspace.policy.max_request_tokens.toLocaleString()} tokens maximum`], ['Retries per call', workspace.policy.max_attempts - 1], ['Evidence memory', 'Relevant source excerpts, rebuilt per task'], ['Durable memory', 'Local SQLite checkpoints and approvals'], ['Preview cache', '12 MB · 12 items · 5 minute TTL'], ['History', '100 recent runs · 150 events per run']].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div></details>
        {activeRun && <section className="surface"><div className="section-heading"><h2>Run {activeRun.id.slice(0, 8)}</h2><IconButton title="Close run details" onClick={() => setActiveRun(null)}><X size={17}/></IconButton></div>{activeRun.telemetry && <div className="stats-grid compact"><Metric label="Model requests" value={activeRun.telemetry.calls}/><Metric label="Elapsed" value={`${activeRun.telemetry.elapsed_seconds}s`}/><Metric label="Provider token usage" value={activeRun.telemetry.responses_with_usage ? activeRun.telemetry.actual_tokens.toLocaleString() : 'Unavailable'}/><Metric label="Estimated reservations" value={activeRun.telemetry.estimated_reserved_tokens.toLocaleString()}/></div>}<div className="event-list">{activeRun.events.map((event, i) => <div key={i}><span>{new Date(event.at * 1000).toLocaleTimeString()}</span><strong>{event.event.replaceAll('_', ' ')}</strong><code>{Object.entries(event).filter(([k]) => !['at', 'event'].includes(k)).map(([k, v]) => `${k}: ${v}`).join(' · ')}</code></div>)}</div></section>}
      </main>}
    </div>
  </div>;
}

class ErrorBoundary extends React.Component {
  state = { error: false };
  static getDerivedStateFromError() { return { error: true }; }
  render() { return this.state.error ? <main className="boot"><h1>The workspace needs a refresh.</h1><p>Your saved approvals are retained.</p><button className="button primary" onClick={() => location.reload()}>Reload workspace</button></main> : this.props.children; }
}
createRoot(document.getElementById('root')).render(<ErrorBoundary><App/></ErrorBoundary>);
