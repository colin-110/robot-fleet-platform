import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// global.css first, on purpose: App.css is the layout layer and has to be able
// to override the design system's base rules. Importing it after App.jsx (which
// pulls in App.css itself) reversed that, so a base rule like `.iconBtn` beat
// the layout rule meant to hide the mobile menu button on desktop.
import "./styles/global.css";

import ErrorBoundary from "./components/ErrorBoundary";
import App from "./App.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>
);
