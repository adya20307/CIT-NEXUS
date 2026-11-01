import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { KeycloakAuthProvider } from './auth/KeycloakAuthProvider.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <KeycloakAuthProvider>
      <App />
    </KeycloakAuthProvider>
  </StrictMode>,
)
