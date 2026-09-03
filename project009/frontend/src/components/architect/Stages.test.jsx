import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import {
  BlueprintStage,
  InterviewStage,
  OpportunitiesStage,
  ProofStage,
  ResearchStage,
  ResultsStage,
  StartStage,
  StartupStage,
} from './Stages'

const research = {
  website: 'https://example.com',
  summary: 'A sourced company summary.',
  facts: [{ label: 'Operating model', value: 'Distribution', source_urls: ['https://example.com'] }],
  sources: [{ label: 'Homepage', url: 'https://example.com' }],
  assumptions: ['Email-based intake is not verified.'],
}

const opportunities = {
  recommended_id: 'vendor-quotation-comparison',
  opportunities: [
    ['vendor-quotation-comparison', 'Quote comparison'],
    ['response-chaser', 'Response chaser'],
    ['spend-analysis', 'Spend analysis'],
  ].map(([id, title], index) => ({
    id, title, rank: index + 1, description: `${title} description`,
    scores: { business_value: 5, feasibility: 4, data_readiness: 4, integration_complexity: 2, operational_risk: 2, time_to_pilot: 2 },
    pilot_weeks_min: 2, pilot_weeks_max: 5, risks: [], assumptions: [],
  })),
}

const blueprint = {
  title: 'Quote Comparison Agent',
  business: { workflow_owner: 'Procurement', target_outcome: 'Decision-ready comparison' },
  agent: { objective: 'Compare quotes' },
  technical: { model_requirements: ['Vision model'] },
  economic: { currency: 'INR', estimated_cost_per_execution: 10 },
  pilot: { scope: 'One category' },
  risks: ['Poor scans'], assumptions: ['Human approval remains required'],
}

const proof = {
  steps_completed: ['identify', 'extract', 'normalize', 'compare', 'flag', 'generate'],
  extractions: [
    { document_name: 'a.pdf', vendor_name: 'A' },
    { document_name: 'b.xlsx', vendor_name: 'B' },
    { document_name: 'c.png', vendor_name: 'C' },
  ],
  comparison_rows: [{ field: 'unit_price', cells: [
    { document_name: 'a.pdf', value: 10, confidence: .96, is_best: true, review_required: false, source: { locator: 'page 1' } },
    { document_name: 'b.xlsx', value: 11, confidence: .95, is_best: false, review_required: false, source: { locator: 'Quote!B2' } },
    { document_name: 'c.png', value: null, confidence: .4, is_best: false, review_required: true, source: { locator: 'image region' } },
  ] }],
  review_flags: [{ category: 'missing', message: 'One value needs review.' }],
  recommendation: 'Recommend A subject to human review.',
}

describe('Agentic Architect stages', () => {
  it('replaces submitted intake with truthful workspace startup messaging', () => {
    const { rerender } = render(<StartStage busy={true} onStart={vi.fn()} />)
    expect(screen.getByText(/requirements are saved/i)).toBeInTheDocument()
    expect(screen.queryByLabelText('Company name')).not.toBeInTheDocument()
    rerender(<StartupStage snapshot={{ status: 'queued' }} />)
    expect(screen.getByText(/assessment is queued/i)).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(/available assessment slot/i)
  })

  it('collects customer requirements instead of a hard-coded company scenario', () => {
    const start = vi.fn()
    render(<StartStage busy={false} onStart={start} />)
    fireEvent.change(screen.getByLabelText('Company name'), { target: { value: 'Northstar Health' } })
    fireEvent.change(screen.getByLabelText('Company website'), { target: { value: 'https://northstar.example' } })
    fireEvent.change(screen.getByLabelText('Your role'), { target: { value: 'Operations Director' } })
    fireEvent.change(screen.getByLabelText('Industry'), { target: { value: 'Healthcare' } })
    fireEvent.change(screen.getByLabelText('Workflow challenge'), { target: { value: 'Triage incoming cases without repeated manual review.' } })
    fireEvent.change(screen.getByLabelText('Desired outcome'), { target: { value: 'Reduce triage time while preserving review.' } })
    fireEvent.change(screen.getByLabelText('Proof objective'), { target: { value: 'Verify case priority and SLA assignment.' } })
    fireEvent.change(screen.getByLabelText(/Fields or evidence to verify/), { target: { value: 'case ID, priority, SLA' } })
    fireEvent.click(screen.getByRole('button', { name: /Start my assessment/i }))
    expect(start).toHaveBeenCalledWith(expect.objectContaining({
      company_name: 'Northstar Health', participant_role: 'Operations Director',
      proof_fields: ['case ID', 'priority', 'SLA'],
    }))
    expect(screen.queryByText(/Meridian/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/24 hours/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/email me/i)).not.toBeInTheDocument()
  })

  it('submits research corrections with user-provided provenance', () => {
    const submit = vi.fn()
    render(<ResearchStage snapshot={{ results: {} }} action={{ payload: { research } }} onSubmit={submit} />)
    fireEvent.change(screen.getByLabelText(/Correction from you/i), { target: { value: 'The operating model is regional.' } })
    fireEvent.click(screen.getByRole('button', { name: /Submit correction/i }))
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({ accepted: false }))
    expect(submit.mock.calls[0][0].corrections[0].provenance).toBe('user_provided')
  })

  it('renders one interview question and lets the customer choose among all opportunities', () => {
    const { unmount } = render(<InterviewStage snapshot={{ results: {} }} action={{ payload: { question: 'How long does review take?', progress: { question_number: 2, confirmed_topics: ['owner'] } } }} onSubmit={() => Promise.resolve()} />)
    expect(screen.getByText(/QUESTION 2 OF 6/i)).toBeInTheDocument()
    unmount()
    const submit = vi.fn()
    render(<OpportunitiesStage snapshot={{ results: {} }} action={{ payload: { opportunities } }} onSubmit={submit} />)
    expect(screen.getAllByRole('article')).toHaveLength(3)
    fireEvent.click(screen.getAllByRole('button', { name: 'Select this opportunity' })[0])
    fireEvent.click(screen.getByRole('button', { name: /Design this agent blueprint/i }))
    expect(submit).toHaveBeenCalledWith({ selected_id: 'response-chaser' })
  })

  it('renders blueprint tabs and the completed proof with source locators', () => {
    const { unmount } = render(<BlueprintStage snapshot={{ results: { blueprint } }} />)
    expect(screen.getByRole('tab', { name: 'business' })).toHaveAttribute('aria-selected', 'true')
    unmount()
    render(<ProofStage snapshot={{ results: { proof }, documents: [] }} />)
    expect(screen.getByText(/page 1/i)).toBeInTheDocument()
    expect(screen.getByText(/human review/i)).toBeInTheDocument()
  })

  it('requires blueprint confirmation before live proof', () => {
    const submit = vi.fn()
    render(<BlueprintStage snapshot={{ results: { blueprint } }} action={{ action_id: 'confirm-blueprint' }} onSubmit={submit} />)
    fireEvent.click(screen.getByRole('button', { name: /Continue to live proof/i }))
    expect(submit).toHaveBeenCalledWith({ accepted: true })
  })

  it('supports proof chat and an explicit skip decision', async () => {
    const submit = vi.fn().mockResolvedValue(true)
    render(<ProofStage
      snapshot={{
        activity: 'awaiting_proof_choice', proof_status: 'not_run', results: {}, documents: [], proof_messages: [],
        requirements: { proof_goal: 'Check case priority.', proof_fields: ['case ID', 'priority'] },
      }}
      action={{ action_id: 'proof-action', payload: { messages_used: 0, message_limit: 3 } }}
      onSubmit={submit}
      onUpload={vi.fn()}
      onRemove={vi.fn()}
    />)
    fireEvent.change(screen.getByLabelText(/Message the agent/i), { target: { value: 'How is low confidence handled?' } })
    fireEvent.click(screen.getByRole('button', { name: /Send message/i }))
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ kind: 'message', message: 'How is low confidence handled?' }))
    fireEvent.click(screen.getByRole('button', { name: /Skip proof and view results/i }))
    expect(submit).toHaveBeenCalledWith({ kind: 'skip' })
    expect(screen.getByRole('button', { name: /Run document proof/i })).toBeDisabled()
  })

  it('renders safe results without deferred delivery controls', () => {
    render(<ResultsStage snapshot={{ assessment_id: 'abc', status: 'completed', results: { blueprint, proof }, usage: { known_cost_usd: .08, input_tokens: 100, output_tokens: 50 } }} />)
    expect(screen.getByText('$0.080')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /download|email|book/i })).not.toBeInTheDocument()
  })

  it('labels skipped proof as blueprint-only without a false proof claim', () => {
    const { container } = render(<ResultsStage snapshot={{ assessment_id: 'skip-1', status: 'completed', proof_status: 'skipped', results: { blueprint }, usage: {} }} />)
    expect(screen.getByText(/Blueprint-only result/i)).toBeInTheDocument()
    expect(screen.getByText(/No documents were analyzed/i)).toBeInTheDocument()
    expect(screen.getByText(/Live proof intentionally skipped/i)).toBeInTheDocument()
    expect(container).not.toHaveTextContent('Customer proof objective tested')
  })
})
