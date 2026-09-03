import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { architectApi } from '../lib/architectApi'
import { AssessmentShell, STAGES } from '../components/architect/AssessmentShell'
import {
  BlueprintStage,
  InterviewStage,
  OpportunitiesStage,
  ProofStage,
  ResearchStage,
  ResultsStage,
  StartStage,
  StartupStage,
  Waiting,
} from '../components/architect/Stages'
import './AgenticArchitectPage.css'

const TERMINAL = new Set(['completed', 'failed', 'cancelled'])

export default function AgenticArchitectPage() {
  const { assessmentId } = useParams()
  const navigate = useNavigate()
  const [snapshot, setSnapshot] = useState(null)
  const [viewedStage, setViewedStage] = useState('start')
  const [connection, setConnection] = useState(assessmentId ? 'connecting' : 'ready')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [transitionDirection, setTransitionDirection] = useState('forward')
  const snapshotRef = useRef(null)
  const viewedStageRef = useRef('start')
  const loadedAssessmentIdRef = useRef(null)
  const stageFrameRef = useRef(null)
  const firstStageRenderRef = useRef(true)

  const moveToStage = useCallback(nextStage => {
    if (!nextStage || nextStage === viewedStageRef.current) return
    const previousIndex = STAGES.findIndex(([id]) => id === viewedStageRef.current)
    const nextIndex = STAGES.findIndex(([id]) => id === nextStage)
    setTransitionDirection(nextIndex >= previousIndex ? 'forward' : 'backward')
    viewedStageRef.current = nextStage
    setViewedStage(nextStage)
  }, [])

  const loadSnapshot = useCallback(async () => {
    if (!assessmentId) return null
    const next = await architectApi.get(assessmentId)
    const previous = snapshotRef.current
    snapshotRef.current = next
    setSnapshot(next)
    if (!previous || viewedStageRef.current === previous.current_stage) moveToStage(next.current_stage)
    return next
  }, [assessmentId, moveToStage])

  useEffect(() => {
    if (!assessmentId) {
      loadedAssessmentIdRef.current = null
      snapshotRef.current = null
      viewedStageRef.current = 'start'
      setSnapshot(null)
      setViewedStage('start')
      setConnection('ready')
      return
    }
    if (loadedAssessmentIdRef.current !== assessmentId) {
      loadedAssessmentIdRef.current = assessmentId
      snapshotRef.current = null
      viewedStageRef.current = 'start'
      setSnapshot(null)
      setViewedStage('start')
      setConnection('connecting')
    }
    let cancelled = false
    let source
    loadSnapshot().then(initial => {
      if (cancelled || !initial || TERMINAL.has(initial.status)) {
        if (initial) setConnection('closed')
        return
      }
      source = new EventSource(`/api/assessments/${assessmentId}/events?after=${initial.last_event_sequence}`)
      source.onopen = () => setConnection('live')
      source.onerror = () => setConnection('reconnecting')
      for (const eventName of ['snapshot', 'assessment_event', 'interaction_required', 'interaction_accepted', 'terminal']) {
        source.addEventListener(eventName, async event => {
          if (cancelled) return
          await loadSnapshot().catch(() => setConnection('reconnecting'))
          if (eventName === 'terminal') {
            setConnection('closed')
            source.close()
            moveToStage('results')
          }
        })
      }
    }).catch(err => setError(err.message))
    return () => { cancelled = true; source?.close() }
  }, [assessmentId, loadSnapshot, moveToStage])

  const perform = async operation => {
    setBusy(true)
    setError('')
    try {
      const result = await operation()
      if (assessmentId) await loadSnapshot()
      return result
    } catch (err) {
      if (err.status === 409 && assessmentId) await loadSnapshot().catch(() => {})
      setError(err.message)
      throw err
    } finally {
      setBusy(false)
    }
  }

  const start = requirements => perform(async () => {
    const created = await architectApi.create(requirements)
    navigate(`/agentic-ai-architect/${created.assessment_id}`)
  }).catch(() => {})

  const action = snapshot?.pending_action
  const submit = async payload => {
    try {
      await perform(() => architectApi.act(assessmentId, action.action_id, payload))
      return true
    } catch {
      return false
    }
  }
  const upload = file => perform(() => architectApi.upload(assessmentId, file)).catch(() => {})
  const remove = documentId => perform(() => architectApi.removeDocument(assessmentId, documentId)).catch(() => {})
  const cancel = () => perform(() => architectApi.cancel(assessmentId)).then(() => moveToStage('results')).catch(() => {})

  const effectiveSnapshot = snapshot || {
    status: assessmentId ? 'loading' : 'ready', current_stage: 'start', activity: assessmentId ? 'queued' : 'ready', proof_status: 'not_run', proof_messages: [], results: {}, documents: [], usage: {}, budget_credit_micros: 1000000,
  }

  let content
  if (assessmentId && !snapshot && !error) content = <Waiting title="Loading assessment" text="Restoring the authoritative server snapshot before reconnecting to live progress." />
  else if (viewedStage === 'start') content = assessmentId
    ? <StartupStage snapshot={effectiveSnapshot} />
    : <StartStage busy={busy} error={error} onStart={start} />
  else if (viewedStage === 'research') content = <ResearchStage snapshot={effectiveSnapshot} action={action?.type === 'confirm_research' ? action : null} onSubmit={submit} busy={busy} error={error} />
  else if (viewedStage === 'interview') content = <InterviewStage snapshot={effectiveSnapshot} action={action?.type === 'answer_interview' ? action : null} onSubmit={submit} busy={busy} error={error} />
  else if (viewedStage === 'opportunities') content = <OpportunitiesStage snapshot={effectiveSnapshot} action={action?.type === 'select_opportunity' ? action : null} onSubmit={submit} busy={busy} error={error} />
  else if (viewedStage === 'blueprint') content = <BlueprintStage snapshot={effectiveSnapshot} action={action?.type === 'confirm_blueprint' ? action : null} onSubmit={submit} busy={busy} error={error} />
  else if (viewedStage === 'proof') content = <ProofStage snapshot={effectiveSnapshot} action={action?.type === 'proof_workspace' ? action : null} onUpload={upload} onRemove={remove} onSubmit={submit} busy={busy} error={error} />
  else content = <ResultsStage snapshot={effectiveSnapshot} />

  const contentKey = viewedStage

  useEffect(() => {
    if (firstStageRenderRef.current) {
      firstStageRenderRef.current = false
      return
    }
    const frame = stageFrameRef.current
    if (!frame) return
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const animationFrame = window.requestAnimationFrame(() => {
      frame.focus({ preventScroll: true })
      frame.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' })
    })
    return () => window.cancelAnimationFrame(animationFrame)
  }, [contentKey])

  return (
    <AssessmentShell snapshot={effectiveSnapshot} viewedStage={viewedStage} onViewStage={moveToStage} connection={connection} onCancel={cancel}>
      <section
        key={contentKey}
        ref={stageFrameRef}
        className={`architect-stage-frame ${transitionDirection}`}
        tabIndex={-1}
        aria-label={`${viewedStage} stage`}
      >
        {content}
      </section>
    </AssessmentShell>
  )
}
