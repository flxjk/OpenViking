// @vitest-environment jsdom
import { ServerConfigurationRoute } from './page'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  mode: 'api_key',
  role: 'root',
  key: 'synthetic-root',
  loading: false,
  ready: true,
  editor: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    serverMode: state.mode,
    connectionRole: state.role,
    connection: { adminApiKey: state.key },
    identityScopeKey: 'synthetic',
    isConnectionRoleLoading: state.loading,
  }),
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ ready: state.ready, provider: 'opensource' }),
}))
vi.mock('./components/server-configuration', () => ({
  ServerConfigurationEditor: () => {
    state.editor()
    return <div data-testid="server-editor" />
  },
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  state.mode = 'api_key'
  state.role = 'root'
  state.key = 'synthetic-root'
  state.loading = false
  state.ready = true
})
it('shows the dev-mode setup prerequisite without mounting configuration requests', () => {
  state.mode = 'dev'
  state.key = ''
  render(<ServerConfigurationRoute />)
  expect(screen.getByRole('status').textContent).toBe(
    'modes.developmentConfigurationSetup',
  )
  expect(state.editor).not.toHaveBeenCalled()
})
it.each([
  { role: 'user', key: 'synthetic-user' },
  { role: 'root', key: '' },
])(
  'does not mount server configuration for $role without a validated Root credential',
  ({ role, key }) => {
    state.role = role
    state.key = key
    render(<ServerConfigurationRoute />)
    expect(screen.getByText('modes.rootRequired')).toBeTruthy()
    expect(state.editor).not.toHaveBeenCalled()
  },
)
it('waits for identity verification before mounting configuration requests', () => {
  state.loading = true
  render(<ServerConfigurationRoute />)
  expect(screen.getByRole('status').textContent).toBe('modes.checking')
  expect(state.editor).not.toHaveBeenCalled()
})
it('mounts server configuration for an authenticated Root identity', () => {
  render(<ServerConfigurationRoute />)
  expect(screen.getByTestId('server-editor')).toBeTruthy()
})
