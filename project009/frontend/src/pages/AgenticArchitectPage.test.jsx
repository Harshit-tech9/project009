import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import AgenticArchitectPage from '../pages/AgenticArchitectPage'

describe('Agentic Architect journey', () => {
  it('renders the start stage inside the directional transition frame', () => {
    render(
      <MemoryRouter initialEntries={['/agentic-ai-architect']}>
        <Routes>
          <Route path="/agentic-ai-architect" element={<AgenticArchitectPage />} />
        </Routes>
      </MemoryRouter>,
    )
    const stage = screen.getByRole('region', { name: 'start stage' })
    expect(stage).toHaveClass('architect-stage-frame', 'forward')
    expect(screen.getByText('Stage 1 of 7')).toBeInTheDocument()
  })
})
