import { useLang } from '@rspress/core/runtime';
import { OpenVikingProvider } from '@openviking/react/client';
import { Console } from '@openviking/react/modules/console';
import '@openviking/react/modules/console/style.css';
import { consoleTransport } from '#console-transport';

const transport = consoleTransport();

export default function ConsoleHook() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  return (
    <OpenVikingProvider fetch={transport}>
      <Console locale={locale} defaultMode="command" />
    </OpenVikingProvider>
  );
}
