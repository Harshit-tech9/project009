import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom'
import AgenticArchitectPage from './pages/AgenticArchitectPage'

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<Navigate to="/agentic-ai-architect" replace />} />
        <Route path="/agentic-ai-architect" element={<AgenticArchitectPage />} />
        <Route path="/agentic-ai-architect/:assessmentId" element={<AgenticArchitectPage />} />
      </Routes>
    </Router>
  )
}

export default App
