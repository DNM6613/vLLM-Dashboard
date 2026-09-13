import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { I18nProvider } from './i18n'
import { applyTheme, getStoredTheme } from './hooks/useTheme'

applyTheme(getStoredTheme())

createRoot(document.getElementById('root')!).render(
  <I18nProvider>
    <App />
  </I18nProvider>,
)
