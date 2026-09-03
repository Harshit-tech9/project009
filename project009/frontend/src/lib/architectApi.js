async function request(path, options = {}) {
  const response = await fetch(path, options)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail = Array.isArray(body.detail)
      ? body.detail.map(item => item.msg || 'Invalid requirement').join('; ')
      : body.detail
    const error = new Error(detail || `Request failed (${response.status})`)
    error.status = response.status
    throw error
  }
  if (response.status === 204) return null
  return response.json()
}

export const architectApi = {
  create: requirements => request('/api/assessments', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requirements),
  }),
  get: assessmentId => request(`/api/assessments/${assessmentId}`),
  act: (assessmentId, actionId, payload) => request(`/api/assessments/${assessmentId}/actions/${actionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),
  upload: (assessmentId, file) => {
    const body = new FormData()
    body.append('file', file)
    return request(`/api/assessments/${assessmentId}/documents`, { method: 'POST', body })
  },
  removeDocument: (assessmentId, documentId) => request(`/api/assessments/${assessmentId}/documents/${documentId}`, { method: 'DELETE' }),
  cancel: assessmentId => request(`/api/assessments/${assessmentId}/cancel`, { method: 'POST' }),
}
