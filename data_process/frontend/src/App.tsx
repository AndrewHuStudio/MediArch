import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { OverviewPanel } from './components/overview/OverviewPanel'
import { OcrPanel } from './components/ocr/OcrPanel'
import { VectorPanel } from './components/vector/VectorPanel'
import { KgPanel } from './components/kg/KgPanel'

export default function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/overview" element={<OverviewPanel />} />
          <Route path="/ocr" element={<OcrPanel />} />
          <Route path="/vector" element={<VectorPanel />} />
          <Route path="/kg" element={<KgPanel />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
