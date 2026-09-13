import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { HomePage } from './pages/HomePage';
import { ApiKeyGate } from './components/ApiKeyGate';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="*" element={<HomePage />} />
      </Routes>
      <ApiKeyGate />
    </BrowserRouter>
  );
}

export default App;
