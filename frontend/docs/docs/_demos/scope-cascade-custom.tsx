import { useLang } from '@rspress/core/runtime';
import { ScopeCascade } from '@openviking/react/modules/scope-cascade';
import '@openviking/react/modules/scope-cascade/style.css';

// Fixed mock APIs, adapted to the same public option shape.
const api = {
  loadLibraries: (_signal: AbortSignal) =>
    Promise.resolve([{ id: 'demo', label: 'Demo library' }]),
  loadSpaces: (_libraryId: string, _signal: AbortSignal) =>
    Promise.resolve([{ id: 'public', label: 'Public space' }]),
  loadUsers: (_libraryId: string, _spaceId: string, _signal: AbortSignal) =>
    Promise.resolve([{ id: 'default', label: 'Default user' }]),
};

export default function ScopeCascadeCustom() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  return (
    <div style={{ minHeight: 320 }}>
      <ScopeCascade
        {...api}
        locale={locale}
        levelNames={['Library', 'Space', 'User']}
        retryLabel={locale === 'zh-CN' ? '重试' : 'Retry'}
      />
    </div>
  );
}
