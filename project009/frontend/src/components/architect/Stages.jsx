import { useEffect, useRef, useState } from 'react'

function StageHeading({ number, kicker, title, children }) {
  return <><div className="stage-kicker">{String(number).padStart(2, '0')} — {kicker}</div><h1>{title}</h1>{children && <p className="stage-lede">{children}</p>}</>
}

function ErrorMessage({ error }) {
  return error ? <div className="architect-alert error" role="alert">{error}</div> : null
}

export function StartStage({ busy, error, onStart }) {
  const [requirements, setRequirements] = useState({
    company_name: '', website: '', participant_role: '', industry: '',
    workflow_challenge: '', desired_outcome: '', proof_goal: '', proof_fields: '',
  })
  const [formError, setFormError] = useState('')
  const update = event => setRequirements(current => ({ ...current, [event.target.name]: event.target.value }))
  const submit = event => {
    event.preventDefault()
    const proofFields = requirements.proof_fields.split(/[\n,]/).map(value => value.trim()).filter(Boolean)
    const uniqueProofFields = proofFields.filter((value, index) => proofFields.findIndex(item => item.toLowerCase() === value.toLowerCase()) === index)
    if (Object.values(requirements).some(value => !value.trim()) || proofFields.length === 0) {
      setFormError('Please complete every field so the assessment can be grounded in your workflow.')
      return
    }
    if (uniqueProofFields.length > 12) {
      setFormError('Use no more than 12 proof fields so the live analysis remains focused.')
      return
    }
    let website
    try { website = new URL(requirements.website) } catch { website = null }
    if (!website || !['http:', 'https:'].includes(website.protocol)) {
      setFormError('Enter a complete website address beginning with https:// or http://.')
      return
    }
    setFormError('')
    onStart({ ...requirements, website: website.toString(), proof_fields: uniqueProofFields })
  }
  if (busy) return <section className="architect-panel"><StageHeading number={1} kicker="Starting assessment" title="Your requirements are saved" /><Waiting title="Creating your assessment" text="We’re preparing the secure workspace and will move you into company research automatically." /></section>
  return <section className="architect-panel">
    <StageHeading number={1} kicker="Your requirements" title="Bring your workflow. Leave with your agent blueprint.">
      Tell us what your company needs. The research, interview, opportunities, blueprint, and proof will be generated from your requirements.
    </StageHeading>
    <form className="architect-card requirements-form" onSubmit={submit}>
      <div className="requirements-grid">
        <label>Company name<input name="company_name" value={requirements.company_name} onChange={update} placeholder="Example: Northstar Health" maxLength={160} /></label>
        <label>Company website<input name="website" type="url" value={requirements.website} onChange={update} placeholder="https://example.com" maxLength={500} /></label>
        <label>Your role<input name="participant_role" value={requirements.participant_role} onChange={update} placeholder="Example: Operations Director" maxLength={160} /></label>
        <label>Industry<input name="industry" value={requirements.industry} onChange={update} placeholder="Example: Healthcare operations" maxLength={160} /></label>
      </div>
      <label>Workflow challenge<textarea name="workflow_challenge" value={requirements.workflow_challenge} onChange={update} placeholder="Describe the process, where it slows down, and who is involved." maxLength={4000} /></label>
      <label>Desired outcome<textarea name="desired_outcome" value={requirements.desired_outcome} onChange={update} placeholder="What should improve, and how will you know it worked?" maxLength={2000} /></label>
      <label>Proof objective<textarea name="proof_goal" value={requirements.proof_goal} onChange={update} placeholder="What should the live proof demonstrate using your sample documents?" maxLength={2000} /></label>
      <label>Fields or evidence to verify<input name="proof_fields" value={requirements.proof_fields} onChange={update} placeholder="Example: case ID, priority, SLA, owner, resolution status" maxLength={1000} /><small>Separate up to 12 fields with commas.</small></label>
      <div className="architect-alert">Public research is sourced; your requirements are labelled customer-provided. Do not upload confidential or regulated documents. Data remains locally stored until manually removed.</div>
      <ErrorMessage error={formError || error} />
      <div className="architect-actions"><button type="submit" className="architect-button primary" disabled={busy}>{busy ? 'Starting…' : 'Start my assessment →'}</button></div>
    </form>
  </section>
}

export function StartupStage({ snapshot }) {
  const queued = snapshot.status === 'queued' || snapshot.status === 'loading'
  return <section className="architect-panel">
    <StageHeading number={1} kicker="Assessment setup" title={queued ? 'Your assessment is queued' : 'Starting your secure AI workspace'}>
      Your requirements are already saved. This page will advance automatically when the workspace is ready.
    </StageHeading>
    <StageActivity
      title={queued ? 'Waiting for an available assessment slot' : 'Starting the assessment server'}
      text={queued ? 'One assessment runs at a time. You can keep this tab open and reconnect safely.' : 'The agent workspace is being verified and started before public-source research begins.'}
    />
  </section>
}

function StageActivity({ title, text, compact = false }) {
  return <div className={`architect-activity ${compact ? 'compact' : ''}`} role="status" aria-live="polite"><span className="architect-spinner" /><div><strong>{title}</strong><p>{text}</p></div></div>
}

export function ResearchStage({ snapshot, action, onSubmit, busy, error }) {
  const research = snapshot.results?.research || action?.payload?.research
  const savedCorrections = snapshot.results?.research_corrections || []
  const [correction, setCorrection] = useState('')
  if (!research) return <Waiting title={`Researching ${snapshot.company_name || 'your company'}`} text="Hermes is reviewing public sources and preparing a sourced company summary." />
  const submit = () => onSubmit(correction.trim() ? { accepted: false, corrections: [{ target: 'summary', corrected_value: correction.trim(), provenance: 'user_provided' }] } : { accepted: true, corrections: [] })
  return <section className="architect-panel">
    <StageHeading number={2} kicker="Company research" title={`Here’s what I found on ${research.website}`}>Review sourced public facts separately from assumptions and your corrections.</StageHeading>
    {snapshot.requirements && <RequirementsSummary requirements={snapshot.requirements} />}
    <div className="architect-message"><span>◆</span><p>{research.summary}</p></div>
    <div className="architect-card">
      <div className="architect-facts">{research.facts.map(fact => <div key={fact.label}><small>{fact.label}</small><strong>{fact.value}</strong></div>)}</div>
      <div className="source-row">{research.sources.map(source => <a key={source.url} href={source.url} target="_blank" rel="noreferrer">↗ {source.label}</a>)}</div>
      {research.assumptions.map(item => <div className="architect-alert warning" key={item}><b>Assumption:</b> {item}</div>)}
      {savedCorrections.map((item, index) => <div className="architect-alert" key={`${item.target}-${index}`}><b>User-provided correction:</b> {item.corrected_value}</div>)}
    </div>
    {action && <div className="architect-card correction-card"><label htmlFor="research-correction">Correction from you <span>(optional, stored as user-provided—not as a public-source claim)</span></label><textarea id="research-correction" value={correction} onChange={e => setCorrection(e.target.value)} placeholder="Explain what should be corrected…" maxLength={4000} /></div>}
    <ErrorMessage error={error} />
    {action && <div className="architect-actions"><button className="architect-button primary" disabled={busy} onClick={submit}>{correction.trim() ? 'Submit correction →' : 'That’s broadly correct →'}</button></div>}
    {!action && ['preparing_interview', 'preparing_question'].includes(snapshot.activity) && <StageActivity title="Research confirmed" text="Preparing the first question for your workflow interview." />}
  </section>
}

export function InterviewStage({ snapshot, action, onSubmit, busy, error }) {
  const [answer, setAnswer] = useState('')
  const [optimisticAnswer, setOptimisticAnswer] = useState(null)
  const chatEndRef = useRef(null)
  const interview = snapshot.results?.interview
  const draft = snapshot.results?.interview_draft
  const messages = draft?.messages || interview?.messages || []
  const currentActionId = action?.action_id
  useEffect(() => {
    if (currentActionId && optimisticAnswer && currentActionId !== optimisticAnswer.actionId) setOptimisticAnswer(null)
  }, [currentActionId, optimisticAnswer])
  useEffect(() => {
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    chatEndRef.current?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'nearest' })
  }, [messages.length, currentActionId, optimisticAnswer])
  const submitAnswer = async () => {
    const submitted = answer.trim()
    if (!submitted) return
    setOptimisticAnswer({ actionId: currentActionId, content: submitted })
    setAnswer('')
    const accepted = await onSubmit({ answer: submitted })
    if (!accepted) {
      setOptimisticAnswer(null)
      setAnswer(submitted)
    }
  }
  return <section className="architect-panel">
    <StageHeading number={3} kicker="Workflow interview" title="A few questions about how this actually happens today">Six questions maximum. Unknowns remain explicit rather than being invented.</StageHeading>
    <div className="architect-chat-log">
    {messages.map((message, index) => <div className={`architect-message ${message.role === 'participant' ? 'user' : ''}`} key={`${message.role}-${index}`}><span>{message.role === 'participant' ? 'You' : 'AI'}</span><p>{message.content}</p></div>)}
    {action && <>
      <div className="question-progress">QUESTION {action.payload.progress.question_number} OF 6 · {action.payload.progress.confirmed_topics.join(', ') || 'DISCOVERY IN PROGRESS'}</div>
      <div className="architect-message architect-message-new" key={action.action_id}><span>AI</span><p>{action.payload.question}</p></div>
      <div className="architect-card answer-card"><label htmlFor="interview-answer">Your answer</label><textarea id="interview-answer" value={answer} onChange={e => setAnswer(e.target.value)} maxLength={4000} autoFocus /></div>
      <ErrorMessage error={error} />
      <div className="architect-actions"><button className="architect-button primary" disabled={busy || !answer.trim()} onClick={submitAnswer}>Submit answer →</button></div>
    </>}
    {optimisticAnswer && !messages.some(message => message.role === 'participant' && message.content === optimisticAnswer.content) && <div className="architect-message user architect-message-new"><span>You</span><p>{optimisticAnswer.content}</p></div>}
    {!action && !interview && <StageActivity title="Preparing the next question" text="The agent is adapting the interview to what you have already confirmed." compact />}
    <div ref={chatEndRef} />
    </div>
    {interview && <WorkflowSummary summary={interview.summary} />}
    {interview && snapshot.activity === 'ranking_opportunities' && <StageActivity title="Workflow interview complete" text="Building and ranking three opportunities for your confirmed workflow." />}
  </section>
}

function WorkflowSummary({ summary }) {
  return <div className="architect-card"><h2>Confirmed workflow summary</h2><div className="architect-facts">
    {Object.entries(summary).map(([key, value]) => <div key={key}><small>{key.replaceAll('_', ' ')}</small><strong>{Array.isArray(value) ? value.join(' · ') : value}</strong></div>)}
  </div></div>
}

export function OpportunitiesStage({ snapshot, action, onSubmit, busy, error }) {
  const data = snapshot.results?.opportunities || action?.payload?.opportunities
  const [selectedId, setSelectedId] = useState(data?.selected_id || data?.recommended_id || '')
  useEffect(() => {
    if (data && !selectedId) setSelectedId(data.selected_id || data.recommended_id || '')
  }, [data, selectedId])
  if (!data) return <section className="architect-panel"><StageHeading number={4} kicker="Opportunity map" title="Building your opportunity map" /><StageActivity title="Ranking three opportunities" text="Business value, feasibility, readiness, complexity, risk, and time-to-pilot are being scored." /></section>
  return <section className="architect-panel">
    <StageHeading number={4} kicker="Opportunity map" title="Three agent opportunities, ranked for your workflow">Choose the opportunity that best fits your requirements. Your stated proof objective and evidence fields remain attached to the assessment.</StageHeading>
    <div className="opportunity-grid">{data.opportunities.map(item => {
      const selected = selectedId === item.id
      return <article className={`opportunity-card ${selected ? 'selected' : ''}`} key={item.id}>
        {item.id === data.recommended_id && <span className="recommended">Recommended</span>}
        <h2>{item.title}</h2><p>{item.description}</p>
        <div className="score-list">{Object.entries(item.scores).map(([name, score]) => <div key={name}><span>{name.replaceAll('_', ' ')}</span><i><b style={{width: `${score * 20}%`}} /></i><em>{score}/5</em></div>)}</div>
        <div className="opportunity-meta"><span>{item.pilot_weeks_min}–{item.pilot_weeks_max} weeks</span><strong>{selected ? 'Selected' : 'Customer choice'}</strong></div>
        {action && <button className="architect-choice" type="button" aria-pressed={selected} onClick={() => setSelectedId(item.id)}>{selected ? 'Selected' : 'Select this opportunity'}</button>}
      </article>
    })}</div>
    <ErrorMessage error={error} />
    {action && <div className="architect-actions"><button className="architect-button primary" disabled={busy || !selectedId} onClick={() => onSubmit({ selected_id: selectedId })}>Design this agent blueprint →</button></div>}
    {!action && snapshot.activity === 'preparing_blueprint' && <StageActivity title="Opportunity selected" text="Preparing the business, agent, technical, economic, and pilot sections of your blueprint." />}
  </section>
}

const BLUEPRINT_TABS = ['business', 'agent', 'technical', 'economic', 'pilot']
export function BlueprintStage({ snapshot, action, onSubmit, busy, error }) {
  const blueprint = snapshot.results?.blueprint
  const [tab, setTab] = useState('business')
  if (!blueprint) return <section className="architect-panel"><StageHeading number={5} kicker="Agent blueprint" title="Designing your agent blueprint" /><StageActivity title="Turning the selected opportunity into an architecture" text="The agent is preparing the business, agent, technical, economic, and pilot sections." /></section>
  const section = blueprint[tab]
  return <section className="architect-panel">
    <StageHeading number={5} kicker="Agent blueprint" title={blueprint.title} />
    <div className="blueprint-tabs" role="tablist">{BLUEPRINT_TABS.map(name => <button role="tab" aria-selected={tab === name} className={tab === name ? 'active' : ''} key={name} onClick={() => setTab(name)}>{name}</button>)}</div>
    <div className="architect-card blueprint-values">{Object.entries(section).map(([key, value]) => <div key={key}><small>{key.replaceAll('_', ' ')}</small><strong>{Array.isArray(value) ? value.join(' · ') : value ?? 'Not yet estimated'}</strong></div>)}</div>
    {(blueprint.risks?.length || blueprint.assumptions?.length) && <div className="architect-two-col"><ListCard title="Risks" values={blueprint.risks} /><ListCard title="Assumptions" values={blueprint.assumptions} /></div>}
    <ErrorMessage error={error} />
    {action && <div className="architect-actions blueprint-confirm"><span>Review all five tabs before continuing.</span><button className="architect-button primary" disabled={busy} onClick={() => onSubmit({ accepted: true })}>Continue to live proof →</button></div>}
    {!action && ['preparing_proof', 'awaiting_proof_choice'].includes(snapshot.activity) && <StageActivity title="Blueprint confirmed" text="Preparing your live proof workspace. You will be able to ask questions, upload examples, run the proof, or skip it." />}
  </section>
}

export function ProofStage({ snapshot, action, onUpload, onRemove, onSubmit, busy, error }) {
  const [message, setMessage] = useState('')
  const docs = snapshot.documents || []
  const proof = snapshot.results?.proof
  const messages = snapshot.proof_messages || []
  const proofGoal = snapshot.requirements?.proof_goal || 'Evaluate the uploaded examples against the selected workflow.'
  const proofFields = snapshot.requirements?.proof_fields || []
  const formats = ['pdf', 'xlsx', 'png']
  const ready = new Set(docs.map(doc => doc.format)).size === 3
  const messagesUsed = messages.filter(item => item.role === 'participant').length
  const messageLimit = action?.payload?.message_limit || 3
  const analyzing = snapshot.activity === 'analyzing_documents'
  const answering = snapshot.activity === 'answering_proof_message'
  const submitMessage = async () => {
    const submitted = message.trim()
    if (!submitted) return
    setMessage('')
    const accepted = await onSubmit({ kind: 'message', message: submitted })
    if (!accepted) setMessage(submitted)
  }
  if (proof) return <ProofResult proof={proof} proofGoal={proofGoal} messages={messages} />
  if (snapshot.proof_status === 'skipped') return <section className="architect-panel"><StageHeading number={6} kicker="Live proof-of-work" title="Live proof was skipped">Your blueprint remains complete. No documents were analyzed and no proof findings were created.</StageHeading><ProofConversation messages={messages} /><div className="architect-alert warning">Proof status: skipped by participant.</div></section>
  return <section className="architect-panel">
    <StageHeading number={6} kicker="Live proof-of-work" title="Test your proof objective on real examples">Upload one PDF, one XLSX, and one PNG that represent your workflow. Each file must be 10 MB or smaller.</StageHeading>
    <div className="architect-card proof-brief"><small>Your proof objective</small><p>{proofGoal}</p>{proofFields.length > 0 && <div className="proof-field-list">{proofFields.map(field => <span key={field}>{field}</span>)}</div>}</div>
    <div className={`proof-steps ${analyzing ? 'active' : ''}`}>{['Identify','Extract','Normalize','Compare','Flag','Generate'].map((label, index) => <div key={label}><span>{index + 1}</span><small>{label}</small></div>)}</div>
    <div className="architect-proof-workspace">
      <div className="architect-card proof-chat">
        <div className="proof-chat-head"><div><h2>Ask the proof agent</h2><p>Clarify scope and expected evidence before the run. The agent cannot inspect file contents until analysis starts.</p></div><span>{messagesUsed}/{messageLimit}</span></div>
        <ProofConversation messages={messages} />
        {answering && <StageActivity title="The proof agent is responding" text="Your message is being answered in the same assessment session." compact />}
        {action && messagesUsed < messageLimit && <div className="proof-composer"><label htmlFor="proof-message">Message the agent</label><textarea id="proof-message" value={message} onChange={event => setMessage(event.target.value)} placeholder="Ask how the proof will evaluate your examples…" maxLength={2000} /><button className="architect-button" disabled={busy || !message.trim()} onClick={submitMessage}>Send message</button></div>}
        {messagesUsed >= messageLimit && <div className="architect-alert">The three-message proof conversation is complete. You can now run or skip the proof.</div>}
      </div>
      <div className="architect-card proof-documents"><h2>Sample documents</h2><p>Files stay local and private until you start the proof.</p>
    {action && <div className="architect-card upload-grid">{formats.map(format => {
      const document = docs.find(item => item.format === format)
      return <div className="upload-slot" key={format}><strong>{format.toUpperCase()}</strong>{document ? <><span>{document.name}</span><small>{(document.size_bytes / 1024).toFixed(1)} KB</small><button onClick={() => onRemove(document.document_id)}>Remove</button></> : <label><span>Choose {format.toUpperCase()}</span><input type="file" accept={format === 'xlsx' ? '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : `.${format}`} onChange={e => e.target.files[0] && onUpload(e.target.files[0])} /></label>}</div>
    })}</div>}
      {!action && docs.length > 0 && <div className="proof-file-summary">{docs.map(doc => <span key={doc.document_id}>{doc.format.toUpperCase()} · {doc.name}</span>)}</div>}
      </div>
    </div>
    {analyzing && <StageActivity title="Proof analysis is active" text="The agent is processing and auditing all six proof steps. Completed step timing will not be fabricated." />}
    {!action && !answering && !analyzing && snapshot.activity === 'preparing_results' && <StageActivity title="Preparing your results" text="The proof was skipped. Finalizing the blueprint-only assessment and cleaning up the workspace." />}
    {!action && !answering && !analyzing && snapshot.activity === 'awaiting_proof_choice' && <StageActivity title="Opening your proof workspace" text="Preparing secure upload and conversation controls." />}
    <ErrorMessage error={error} />
    {action && <div className="architect-actions proof-actions"><button className="architect-button ghost" disabled={busy} onClick={() => onSubmit({ kind: 'skip' })}>Skip proof and view results</button><button className="architect-button primary" disabled={busy || !ready} onClick={() => onSubmit({ kind: 'run', document_ids: docs.map(doc => doc.document_id) })}>Run document proof →</button></div>}
  </section>
}

function ProofConversation({ messages = [] }) {
  if (!messages.length) return <div className="proof-chat-empty">Ask up to three questions before deciding whether to run the proof.</div>
  return <div className="proof-chat-messages">{messages.map(item => <div className={`proof-chat-message ${item.role === 'participant' ? 'user' : 'agent'}`} key={item.message_id}><small>{item.role === 'participant' ? 'You' : 'Proof agent'}</small><p>{item.content}</p></div>)}</div>
}

function ProofResult({ proof, proofGoal, messages }) {
  return <section className="architect-panel">
    <StageHeading number={6} kicker="Live proof-of-work" title="Your document proof is complete">The result addresses your stated objective: {proofGoal}</StageHeading>
    <ProofConversation messages={messages} />
    <div className="proof-steps complete">{proof.steps_completed.map((label, index) => <div key={label}><span>✓</span><small>{label}</small></div>)}</div>
    <div className="comparison-wrap"><table className="comparison-table"><thead><tr><th>Field</th>{proof.extractions.map(item => <th key={item.document_name}>{item.subject_name || item.vendor_name || item.document_name}</th>)}</tr></thead><tbody>{proof.comparison_rows.map(row => <tr key={row.field}><th>{row.field.replaceAll('_',' ')}</th>{row.cells.map(cell => <td className={`${cell.is_best ? 'best' : ''} ${cell.review_required ? 'flagged' : ''}`} key={cell.document_name}><strong>{cell.value ?? 'Not stated'}</strong><small>{Math.round(cell.confidence * 100)}% · {cell.source?.locator || 'No source'}</small></td>)}</tr>)}</tbody></table></div>
    {proof.review_flags.map((flag, index) => <div className="architect-alert warning" key={index}><b>{flag.category.replaceAll('_',' ')}:</b> {flag.message}</div>)}
    <div className="architect-card recommendation"><small>Advisory recommendation</small><p>{proof.recommendation}</p></div>
  </section>
}

export function ResultsStage({ snapshot }) {
  const { results = {}, usage = {} } = snapshot
  const proofStatus = snapshot.proof_status || (results.proof ? 'completed' : 'not_run')
  const proofCompleted = proofStatus === 'completed'
  const proofSkipped = proofStatus === 'skipped'
  const proofFailed = proofStatus === 'failed'
  const preparing = !['completed', 'failed', 'cancelled'].includes(snapshot.status)
  const [copied, setCopied] = useState(false)
  const copy = async () => { await navigator.clipboard.writeText(snapshot.assessment_id); setCopied(true) }
  const checklist = [
    'Company research reviewed',
    'Workflow interview confirmed',
    'Three opportunities ranked',
    'Five-part blueprint generated',
    proofCompleted ? 'Customer proof objective tested' : proofSkipped ? 'Live proof intentionally skipped' : proofFailed ? 'Live proof attempted but not completed' : 'Live proof was not run',
    proofCompleted ? 'Sources and review flags preserved' : 'Blueprint risks and assumptions preserved',
  ]
  return <section className="architect-panel">
    <StageHeading number={7} kicker="Results" title={snapshot.status === 'completed' ? 'Your assessment is ready' : preparing ? 'Preparing your assessment results' : `Assessment ${snapshot.status}`}>This is an advisory architecture and proof result—not an approval or autonomous business decision.</StageHeading>
    {preparing && <StageActivity title="Finalizing the assessment" text="The result is being saved and the secure agent workspace is being cleaned up before completion." />}
    {snapshot.failure_reason && <div className="architect-alert error"><b>Outcome:</b> {snapshot.failure_reason}</div>}
    {proofSkipped && <div className="architect-alert warning" role="status"><b>Blueprint-only result:</b> You chose to skip the live proof. No documents were analyzed, no proof findings were created, and the proof objective is not marked as tested.</div>}
    {proofFailed && <div className="architect-alert error" role="status"><b>Proof incomplete:</b> The blueprint is preserved, but the document proof did not complete. No successful proof outcome is claimed.</div>}
    {snapshot.requirements && <RequirementsSummary requirements={snapshot.requirements} />}
    <div className="result-metrics"><div><small>Known cost</small><strong>${(usage.known_cost_usd || 0).toFixed(3)}</strong></div><div><small>Input tokens</small><strong>{(usage.input_tokens || 0).toLocaleString()}</strong></div><div><small>Output tokens</small><strong>{(usage.output_tokens || 0).toLocaleString()}</strong></div><div><small>Proof status</small><strong className="proof-status-value">{proofStatus.replaceAll('_', ' ')}</strong></div></div>
    {results.blueprint && <div className="architect-card"><h2>{results.blueprint.title}</h2><p>{results.blueprint.business.target_outcome}</p><div className="result-checklist">{checklist.map((item, index) => <div className={index >= 4 && !proofCompleted ? 'qualified' : ''} key={item}>{index >= 4 && !proofCompleted ? '—' : '✓'} <span>{item}</span></div>)}</div></div>}
    {results.blueprint && <div className="architect-two-col"><ListCard title="Risks" values={results.blueprint.risks} /><ListCard title="Assumptions" values={results.blueprint.assumptions} /></div>}
    {results.proof?.discrepancies?.length > 0 && <ListCard title="Proof discrepancies" values={results.proof.discrepancies} />}
    <div className="architect-actions"><button className="architect-button primary" onClick={copy}>{copied ? 'Assessment ID copied' : 'Copy assessment ID'}</button><button className="architect-button ghost" onClick={() => window.location.assign('/agentic-ai-architect')}>Start a new local assessment</button></div>
  </section>
}

function Waiting({ title, text, compact = false }) {
  return <div className={`architect-waiting ${compact ? 'compact' : ''}`} role="status" aria-live="polite"><span className="architect-spinner" /><div><strong>{title}</strong><p>{text}</p></div></div>
}

function ListCard({ title, values = [] }) {
  return <div className="architect-card"><h2>{title}</h2><ul>{values.map(value => <li key={value}>{value}</li>)}</ul></div>
}

function RequirementsSummary({ requirements }) {
  return <div className="architect-card requirements-summary"><h2>Your assessment brief</h2><div className="architect-facts">
    <div><small>Company</small><strong>{requirements.company_name}</strong></div>
    <div><small>Your role</small><strong>{requirements.participant_role}</strong></div>
    <div><small>Industry</small><strong>{requirements.industry}</strong></div>
    <div><small>Desired outcome</small><strong>{requirements.desired_outcome}</strong></div>
  </div><div className="workflow-brief"><small>Workflow challenge</small><p>{requirements.workflow_challenge}</p></div><div className="workflow-brief"><small>Proof objective</small><p>{requirements.proof_goal}</p></div></div>
}

export { ErrorMessage, Waiting }
