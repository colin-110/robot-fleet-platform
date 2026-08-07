import { MapContainer, TileLayer, Marker, Popup, Circle } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import L from "leaflet";
import iconUrl from "leaflet/dist/images/marker-icon.png";
import iconShadow from "leaflet/dist/images/marker-shadow.png";

import useTheme from "../hooks/useTheme";

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

// Carto ships a light and a dark basemap at the same tile scheme, so the map
// follows the console's theme instead of staying a dark rectangle on a light
// page.
const TILES = {
  dark: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
  light: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
};

// Literal, not a CSS token: Leaflet writes these straight onto the SVG path,
// and this red reads the same against either basemap.
const ZONE_COLOR = "#ef4444";

function FleetMap({ robots }) {
  const { theme } = useTheme();

  // Geofence center (e.g. Restricted Area) mapped from simulator coords
  const restrictedZone = [CENTER_LAT + (10 * 0.0001), CENTER_LNG + (15 * 0.0001)];

  return (
    <div className="glassStrong" style={{ padding: 0, height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div className="panelHead">
        <h2>Live Fleet Map</h2>
        <span className="subtle">{robots.length} tracked</span>
      </div>

      <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
        <MapContainer center={[CENTER_LAT, CENTER_LNG]} zoom={17} scrollWheelZoom={false} style={{ height: "100%", width: "100%" }}>
          <TileLayer
            // Keyed so a theme change swaps the layer rather than leaving the
            // previous basemap mounted underneath.
            key={theme}
            url={TILES[theme] || TILES.dark}
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

          <Circle center={restrictedZone} radius={50} pathOptions={{ color: ZONE_COLOR, fillColor: ZONE_COLOR, fillOpacity: 0.2 }}>
            <Popup>Restricted Area</Popup>
          </Circle>
        </MapContainer>
      </div>
    </div>
  );
}

export default FleetMap;
