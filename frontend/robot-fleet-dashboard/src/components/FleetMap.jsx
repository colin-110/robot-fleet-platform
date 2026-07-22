import { MapContainer, TileLayer, Marker, Popup, Circle } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import L from "leaflet";
import iconUrl from "leaflet/dist/images/marker-icon.png";
import iconShadow from "leaflet/dist/images/marker-shadow.png";

// Fix standard icon issue with Webpack/Vite
let DefaultIcon = L.icon({
  iconUrl,
  shadowUrl: iconShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  tooltipAnchor: [16, -28],
  shadowSize: [41, 41]
});
L.Marker.prototype.options.icon = DefaultIcon;

const CENTER_LAT = 37.7749;
const CENTER_LNG = -122.4194;

function FleetMap({ robots }) {
  // Geofence center (e.g. Restricted Area) mapped from simulator coords
  const restrictedZone = [CENTER_LAT + (10 * 0.0001), CENTER_LNG + (15 * 0.0001)];

  return (
    <div className="glassStrong" style={{ padding: 0, height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div className="sectionTitle drag-handle" style={{ padding: "16px 16px 0 16px", cursor: "grab", flexShrink: 0 }}>
        <h2 style={{ margin: 0, fontSize: "16px", color: "rgba(226, 232, 240, 0.96)" }}>Live Fleet Map</h2>
        <span className="subtle">
          {robots.length} active robots
        </span>
      </div>

      <div style={{ flexGrow: 1, position: "relative", marginTop: 8 }}>
        <MapContainer center={[CENTER_LAT, CENTER_LNG]} zoom={17} style={{ height: "100%", width: "100%" }}>
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            attribution="&copy; <a href='https://carto.com/'>CartoDB</a>"
          />
          {robots.map(robot => {
            // Simulator Y maps to Latitude, X maps to Longitude
            if (!isFinite(robot.y) || !isFinite(robot.x)) return null;
            const lat = CENTER_LAT + (robot.y * 0.0001);
            const lng = CENTER_LNG + (robot.x * 0.0001);
            return (
              <Marker key={robot.robot_id} position={[lat, lng]}>
                <Popup>
                  <strong>Robot {robot.robot_id}</strong><br />
                  Status: {robot.status}<br />
                  Battery: {robot.battery}%<br />
                  Speed: {robot.speed} m/s
                </Popup>
              </Marker>
            )
          })}
          
          <Circle center={restrictedZone} radius={50} pathOptions={{ color: '#ef4444', fillColor: '#ef4444', fillOpacity: 0.2 }}>
            <Popup>Restricted Area</Popup>
          </Circle>
        </MapContainer>
      </div>
    </div>
  );
}

export default FleetMap;
