import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { HomePage } from './pages/HomePage';
import { ApiKeyGate } from './components/ApiKeyGate';
import { ErrorBoundary } from './components/ui/ErrorBoundary';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<ErrorBoundary><HomePage /></ErrorBoundary>} />
        <Route path="*" element={<ErrorBoundary><HomePage /></ErrorBoundary>} />
      </Routes>
      <ApiKeyGate />
    </BrowserRouter>
  );
}

export default App;
