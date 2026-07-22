<div align="center">
  <h1>Robot Fleet Platform: Frontend Dashboard</h1>
  <p><strong>A high-performance, real-time Single Page Application built with React 18, Vite, and Redux Toolkit.</strong></p>
</div>

---

## Overview

The **FleetOps Dashboard** serves as the interactive control center for the Robot Fleet Platform. Engineered to ingest thousands of WebSocket telemetry events per second, it renders live geographical data and complex statistical charts synchronously without degrading browser frame rates.

Designed utilizing modern UI/UX principles, the dashboard prioritizes situational awareness, immediately surfacing predictive maintenance warnings and hardware anomalies to fleet operators.

## Core Features

*   **Real-Time Interactive Map (Leaflet.js)**
    *   Tracks precise geospatial coordinates of the entire robotic fleet.
    *   Dynamic map markers continuously update robot status (Active, Charging, Low Power, Overheating) applying strict color-coded taxonomy.
*   **Live Hardware Analytics (Recharts)**
    *   Renders real-time aggregations of fleet-wide battery health, thermal distributions, and component degradation.
    *   Sub-50ms reactivity ensures visualizations synchronize instantaneously with the incoming WebSocket data stream.
*   **Predictive Maintenance Alerting**
    *   Dedicated alerting mechanisms notify operators of Machine Learning anomalies (e.g., sudden battery depletion or critical thermal threshold breaches) calculated by the backend engine.
*   **Performance Optimization (Vite + React 18)**
    *   Leverages React 18's concurrent rendering features and aggressive component memoization to ensure that continuous state mutations (500+ updates per second) do not cause rendering bottlenecks or freeze the main thread.

---

## Technology Stack

*   **Core Framework**: [React 18](https://reactjs.org/) + TypeScript integration
*   **Build Environment**: [Vite](https://vitejs.dev/) (Optimized for rapid Hot Module Replacement and minimal production bundles)
*   **State Management**: [Redux Toolkit](https://redux-toolkit.js.org/) (Centralized, predictable state mutations)
*   **Geospatial Mapping**: [Leaflet.js](https://leafletjs.com/) + `react-leaflet`
*   **Data Visualization**: [Recharts](https://recharts.org/) (Declarative, component-based charting)
*   **Styling Architecture**: Vanilla CSS leveraging modern Flexbox/Grid layouts and dynamic custom properties.

---

## Local Development Setup

### 1. Prerequisites
Ensure [Node.js](https://nodejs.org/) (v16 or higher) is installed on the host machine.

### 2. Dependency Installation
Navigate to the frontend directory and install the required NPM packages. 
*(Note: On Windows PowerShell environments, use `npm.cmd` if the execution policy restricts the standard `npm` script).*

```powershell
cd frontend/robot-fleet-dashboard
npm.cmd install
```

### 3. Environment Configuration
The application automatically resolves the backend API endpoints. If the backend services are hosted on a non-standard port or external host, update the API configuration within the Redux slices or environment variables. (The default target is `http://localhost:8000`).

### 4. Development Server Execution
Initialize the Vite development server:

```powershell
npm.cmd run dev -- --host
```

Access the dashboard via a standard web browser at:
**`http://localhost:5173`**

---

## Architecture and State Management Strategies

Managing high-frequency WebSocket traffic within a React application necessitates strict architectural patterns to prevent catastrophic re-render cascading:

1. **Throttled Dispatches:** Incoming WebSocket telemetry streams are computationally throttled and batched before mutating the Redux store, safeguarding the 60FPS rendering target.
2. **Selective Subscription:** UI Components utilize highly-specific Redux selectors (`useSelector`) to guarantee they only trigger a re-render lifecycle when their explicit subset of data is modified (e.g., isolating a single agent's battery scalar).
3. **Optimistic Updates:** When fleet managers dispatch operational commands (e.g., "Return to Base"), the user interface updates optimistically to ensure immediate perceived responsiveness, prior to receiving the strict state machine acknowledgment from the API backend.

---

## License
This project is licensed under the MIT License.
