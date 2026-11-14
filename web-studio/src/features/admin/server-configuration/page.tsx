import { ServerConfigurationEditor } from '#/features/admin/server-configuration/components/server-configuration'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { canEditStudioConfiguration } from '#/features/connection/lib/studio-permissions'
import { useTranslation } from 'react-i18next'

export function ServerConfigurationRoute() {
  const { t } = useTranslation('appShell')
  const {
    connection,
    connectionRole,
    identityScopeKey,
    isConnectionRoleLoading,
    serverMode,
  } = useAppConnection()
  const service = useStudioService()
  if (isConnectionRoleLoading || !service.ready)
    return <p role="status">{t('modes.checking')}</p>
  if (serverMode === 'dev')
    return <p role="status">{t('modes.developmentConfigurationSetup')}</p>
  if (
    !canEditStudioConfiguration({
      serviceProvider: service.provider,
      hasControlCredential: Boolean(connection.adminApiKey.trim()),
      isRoleLoading: isConnectionRoleLoading,
      role: connectionRole,
      serverMode,
    })
  )
    return <p>{t('modes.rootRequired')}</p>
  return <ServerConfigurationEditor key={identityScopeKey} />
}
