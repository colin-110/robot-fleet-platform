import { Fragment } from "react";
import { MapContainer, TileLayer, Marker, Popup, Circle } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import L from "leaflet";
import iconUrl from "leaflet/dist/images/marker-icon.png";
import iconShadow from "leaflet/dist/images/marker-shadow.png";

import useTheme from "../hooks/useTheme";
import useAnimatedRobots from "../hooks/useAnimatedRobots";

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
// Simulator metres to degrees, and back to Leaflet's metres for radii.
const DEG_PER_UNIT = 0.0001;
const METRES_PER_UNIT = DEG_PER_UNIT * 111_320;

// CartoDB's free anonymous basemap CDN (dark_all/light_all) was
// discontinued - every tile now comes back as an "API KEY REQUIRED"
// placeholder regardless of referrer or request pattern. OpenStreetMap's
// standard tiles are free with no key and no signup, but ship only one
// (light) style; the dark theme is approximated with a CSS filter on the
// tile pane instead of a second tile source (see global.css).
const TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors";

// Literal, not a CSS token: Leaflet writes these straight onto the SVG path,
// and this red reads the same against either basemap.
const ZONE_COLOR = "#ef4444";
const BOUND_COLOR = "#58a6ff";

const toLatLng = (robot) => [CENTER_LAT + robot.y * DEG_PER_UNIT, CENTER_LNG + robot.x * DEG_PER_UNIT];

function FleetMap({ robots, onWatch }) {
  const { theme } = useTheme();
  // Between keyframes each robot moves along its predicted path.
  const live = useAnimatedRobots(robots);

  // Geofence center (e.g. Restricted Area) mapped from simulator coords
  const restrictedZone = [CENTER_LAT + (10 * DEG_PER_UNIT), CENTER_LNG + (15 * DEG_PER_UNIT)];

  return (
    <div className="glassStrong" style={{ padding: 0, height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div className="panelHead">
        <h2>Live Fleet Map</h2>
        <span className="subtle">{robots.length} tracked</span>
      </div>

      <div className={theme === "dark" ? "mapDarkFilter" : undefined} style={{ flex: 1, minHeight: 0, position: "relative" }}>
        <MapContainer center={[CENTER_LAT, CENTER_LNG]} zoom={17} scrollWheelZoom={false} style={{ height: "100%", width: "100%" }}>
          <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
          {live.map(robot => {
            // Simulator Y maps to Latitude, X maps to Longitude
            if (!isFinite(robot.y) || !isFinite(robot.x)) return null;
            const position = toLatLng(robot);
            const bound = robot.bounds?.position_m;
            return (
              <Fragment key={robot.robot_id}>
                {/* The robot is somewhere in here: its position error bound. */}
                {bound && (
                  <Circle
                    center={position}
                    radius={bound * METRES_PER_UNIT}
                    pathOptions={{ color: BOUND_COLOR, weight: 1, fillOpacity: 0.08 }}
                  />
                )}
                <Marker
                  position={position}
                  eventHandlers={{
                    // Looking at a robot asks the server to watch it more closely.
                    popupopen: () => onWatch?.([robot.robot_id]),
                    popupclose: () => onWatch?.([]),
                  }}
                >
                  <Popup>
                    <strong>Robot {robot.robot_id}</strong><br />
                    Status: {robot.status}<br />
                    Battery: {robot.battery.toFixed(1)}%
                    {robot.bounds && <span className="bound">±{robot.bounds.battery_pct}%</span>}<br />
                    Speed: {robot.speed} m/s<br />
                    {bound && <>Position ±{bound} m</>}
                  </Popup>
                </Marker>
              </Fragment>
            );
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
