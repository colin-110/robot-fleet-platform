<div align="center">
  <h1>🖥️ Robot Fleet Platform: Frontend Dashboard</h1>
  <p><strong>A sleek, high-performance, real-time SPA built with React 18, Vite, and Redux.</strong></p>
</div>

---

## 📖 Overview

The **FleetOps Dashboard** is the interactive control center for the Robot Fleet Platform. It ingests thousands of WebSocket telemetry events per second and renders them seamlessly onto a live interactive map and statistical charts—without freezing the browser.

Designed with modern UI/UX principles, the dashboard focuses on situational awareness, alerting fleet managers to predictive maintenance warnings and hardware anomalies in real-time.

## ✨ Core Features & UI Highlights

*   🗺️ **Real-Time Interactive Map (Leaflet.js)**
    *   Tracks the precise geolocation (X/Y coordinates) of the entire fleet.
    *   Dynamic map markers indicate robot status (Active, Charging, Low Power, Overheating) with color-coded alerts.
*   📈 **Live Hardware Analytics (Recharts)**
    *   Plots fleet-wide battery health, thermal distributions, and component degradation over time.
    *   Sub-50ms reactivity ensures charts update instantaneously with incoming WebSocket streams.
*   🧠 **Predictive Maintenance Alerts**
    *   Surface-level toasts and dedicated alert panels notify operators of Machine Learning Z-Score anomalies (e.g., sudden battery drops or critical thermal spikes).
*   🚀 **Performance First (Vite + React 18)**
    *   Utilizes React 18's concurrent features and strict component memoization to ensure that pushing 500+ state updates per second doesn't drop rendering frames.

---

## 🛠️ Tech Stack

*   **Core**: [React 18](https://reactjs.org/) + TypeScript support
*   **Build Tool**: [Vite](https://vitejs.dev/) (blazing fast HMR and optimized production bundles)
*   **State Management**: [Redux Toolkit](https://redux-toolkit.js.org/) (for predictable, centralized state)
*   **Mapping**: [Leaflet.js](https://leafletjs.com/) + `react-leaflet`
*   **Charting**: [Recharts](https://recharts.org/) (composable charting library)
*   **Styling**: Vanilla CSS with modern Flexbox/Grid layouts and dynamic variables.

---

## 💻 Local Development Setup

### 1. Prerequisites
Ensure you have [Node.js](https://nodejs.org/) (v16+) installed.

### 2. Install Dependencies
Navigate to the frontend directory and run npm install. 
*(Note: On Windows PowerShell, use `npm.cmd` if your execution policy blocks the standard `npm` script).*

```powershell
cd frontend/robot-fleet-dashboard
npm.cmd install
```

### 3. Environment Configuration
The frontend automatically connects to the backend API. If you are running the backend on a custom host/port, update the API URLs in the Redux slices or environment variables. (By default, it looks for `http://localhost:8000`).

### 4. Run the Development Server
Start the Vite development server with Hot Module Replacement (HMR):

```powershell
npm.cmd run dev -- --host
```

Access the dashboard in your browser at:
**[http://localhost:5173](http://localhost:5173)**

---

## 🏗️ Architecture & State Management

Handling massive volumes of WebSocket traffic in a React application requires strict architectural patterns to prevent re-render cascading:

1. **Throttled Dispatches:** Incoming WebSocket telemetry is throttled/batched before being dispatched to the Redux store to maintain 60FPS UI rendering.
2. **Selective Subscription:** Components use highly-specific Redux selectors (`useSelector`) to ensure they only re-render when their specific subset of data changes (e.g., a single robot's battery percentage).
3. **Optimistic Updates:** When fleet managers dispatch commands (e.g., "Return to Base"), the UI updates optimistically while awaiting the strict state machine acknowledgment from the API.

---

## 📄 License
This project is licensed under the MIT License.
