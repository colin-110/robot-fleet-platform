/**
 * LoadingSkeleton — pulse-animated placeholders shown while data is loading.
 *
 * Usage:
 *   <LoadingSkeleton type="stats" />   — 8 stat cards
 *   <LoadingSkeleton type="cards" />   — 6 robot cards
 *   <LoadingSkeleton type="panel" />   — single panel placeholder
 */

const shimmerStyle = {
  background: "linear-gradient(90deg, rgba(255,255,255,0.05) 25%, rgba(255,255,255,0.1) 50%, rgba(255,255,255,0.05) 75%)",
  backgroundSize: "200% 100%",
  animation: "shimmer 1.5s ease-in-out infinite",
  borderRadius: 8,
};

function SkeletonBlock({ width = "100%", height = 16, style = {} }) {
  return (
    <div
      style={{
        ...shimmerStyle,
        width,
        height,
        ...style,
      }}
    />
  );
}

function StatsSkeleton() {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 16 }}>
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="glass" style={{ padding: "16px 20px" }}>
          <SkeletonBlock width="60%" height={12} style={{ marginBottom: 12 }} />
          <SkeletonBlock width="40%" height={28} />
        </div>
      ))}
    </div>
  );
}

function CardsSkeleton() {
  return (
    <div className="dashboard-grid">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="col-span-4">
          <div className="glass" style={{ padding: 20, minHeight: 180 }}>
            <SkeletonBlock width="30%" height={14} style={{ marginBottom: 16 }} />
            <SkeletonBlock width="100%" height={10} style={{ marginBottom: 10 }} />
            <SkeletonBlock width="80%" height={10} style={{ marginBottom: 10 }} />
            <SkeletonBlock width="60%" height={10} style={{ marginBottom: 16 }} />
            <SkeletonBlock width="100%" height={40} />
          </div>
        </div>
      ))}
    </div>
  );
}

function PanelSkeleton() {
  return (
    <div className="glass" style={{ padding: 24, minHeight: 300 }}>
      <SkeletonBlock width="40%" height={18} style={{ marginBottom: 20 }} />
      <SkeletonBlock width="100%" height={200} />
    </div>
  );
}

export default function LoadingSkeleton({ type = "stats" }) {
  switch (type) {
    case "stats":
      return <StatsSkeleton />;
    case "cards":
      return <CardsSkeleton />;
    case "panel":
      return <PanelSkeleton />;
    default:
      return <PanelSkeleton />;
  }
}
