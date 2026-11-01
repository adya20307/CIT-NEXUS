import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  BarChart3,
  Building2,
  CalendarCheck,
  RefreshCw,
  Search,
} from "lucide-react";
import { apiFetch } from "./auth/keycloakClient.js";
import "./TimetablePage.css";
import "./AnalyticsPage.css";

function formatNumber(value) {
  return new Intl.NumberFormat().format(value);
}

function UtilizationBar({ value, label }) {
  return (
    <div
      className="utilization-bar"
      role="meter"
      aria-label={label}
      aria-valuemin="0"
      aria-valuemax="100"
      aria-valuenow={value}
    >
      <span style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
    </div>
  );
}

function AnalyticsPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [roomType, setRoomType] = useState("ALL");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function loadAnalytics() {
      setLoading(true);
      setError("");
      try {
        const response = await apiFetch("/api/analytics/room-utilization");
        const payload = await response.json();
        if (!response.ok || payload.status !== "success") {
          const detail = typeof payload.detail === "string"
            ? payload.detail
            : payload.detail?.message;
          throw new Error(detail || "Could not load room utilization analytics.");
        }
        if (!cancelled) setData(payload.data);
      } catch (loadError) {
        if (!cancelled) setError(loadError.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadAnalytics();
    return () => {
      cancelled = true;
    };
  }, [reload]);

  const filteredRooms = useMemo(() => {
    if (!data) return [];
    const query = search.trim().toLowerCase();
    return data.rooms.filter(room => {
      const matchesQuery = !query ||
        `${room.room_id} ${room.room_name} ${room.building}`.toLowerCase().includes(query);
      return matchesQuery && (roomType === "ALL" || room.room_type === roomType);
    });
  }, [data, roomType, search]);

  return (
    <section className="analytics-page">
      <div className="welcome analytics-page-heading">
        <div>
          <span className="eyebrow">CAMPUS RESOURCE INTELLIGENCE</span>
          <h1>Room Utilization</h1>
          <p>Utilization is calculated from the official timetable and approved event reservations.</p>
        </div>
        <button
          type="button"
          className="analytics-refresh"
          onClick={() => setReload(value => value + 1)}
          disabled={loading}
        >
          <RefreshCw size={16} />
          Refresh
        </button>
      </div>

      {loading && <div className="panel timetable-state">Calculating utilization from campus data…</div>}
      {!loading && error && (
        <div className="timetable-alert error" role="alert">
          <AlertCircle size={19} />
          <div>
            <strong>Room analytics unavailable</strong>
            <p>{error}</p>
            <button type="button" onClick={() => setReload(value => value + 1)}>Try again</button>
          </div>
        </div>
      )}

      {!loading && data && (
        <>
          <div className="analytics-summary-grid">
            <article className="analytics-summary-card">
              <span><Building2 size={16} /> Room inventory</span>
              <strong>{data.rooms.length}</strong>
              <small>Across {data.by_room_type.length} room types</small>
            </article>
            <article className="analytics-summary-card teaching">
              <span><BarChart3 size={16} /> Teaching utilization</span>
              <strong>{data.teaching.utilization_percent}%</strong>
              <small>
                {formatNumber(data.teaching.occupied_periods)} occupied of{" "}
                {formatNumber(data.teaching.available_periods)} available teaching periods
              </small>
            </article>
            <article className="analytics-summary-card events">
              <span><CalendarCheck size={16} /> Approved event utilization</span>
              <strong>{data.approved_events.utilization_percent}%</strong>
              <small>
                {formatNumber(data.approved_events.occupied_periods)} reserved of{" "}
                {formatNumber(data.approved_events.available_periods)} available calendar periods
              </small>
            </article>
            <article className="analytics-summary-card capacity">
              <span><Building2 size={16} /> Available capacity</span>
              <strong>{formatNumber(data.available_capacity_seat_periods)}</strong>
              <small>Available teaching seat-periods in vacant rooms, excluding maintenance</small>
            </article>
          </div>

          <div className="analytics-scope-note">
            <div>
              <strong>Teaching and events use separate utilization measures</strong>
              <p>{data.teaching.denominator}. Event utilization counts approved bookings only and uses all 13 calendar periods per weekday.</p>
            </div>
            <div>
              <strong>Maintenance excluded</strong>
              <p>Room closures are removed from available-period denominators.</p>
            </div>
          </div>

          <div className="analytics-chart-grid">
            <section className="panel analytics-panel">
              <div className="analytics-panel-heading">
                <div>
                  <h2>Utilization by room type</h2>
                  <p>Teaching occupancy, weighted by available room periods</p>
                </div>
              </div>
              <div className="type-utilization-list">
                {data.by_room_type.map(item => (
                  <div className="type-utilization-row" key={item.room_type}>
                    <div className="type-utilization-label">
                      <strong>{item.room_type.replaceAll("_", " ")}</strong>
                      <span>{item.room_count} rooms · {item.utilization_percent}%</span>
                    </div>
                    <UtilizationBar
                      value={item.utilization_percent}
                      label={`${item.room_type} teaching utilization`}
                    />
                    <small>
                      {item.occupied_periods}/{item.available_periods} periods
                    </small>
                  </div>
                ))}
              </div>
            </section>

            <section className="panel analytics-panel">
              <div className="analytics-panel-heading">
                <div>
                  <h2>Most utilized rooms</h2>
                  <p>Highest teaching-period utilization</p>
                </div>
              </div>
              <div className="ranked-room-list">
                {data.most_utilized_rooms.map((room, index) => (
                  <div className="ranked-room-row" key={room.room_id}>
                    <span className="rank-number">{index + 1}</span>
                    <div className="ranked-room-detail">
                      <strong>{room.room_name}</strong>
                      <small>{room.room_id} · {room.room_type.replaceAll("_", " ")}</small>
                    </div>
                    <strong className="ranked-room-rate">{room.teaching_utilization_percent}%</strong>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <section className="panel analytics-panel underutilized-panel">
            <div className="analytics-panel-heading">
              <div>
                <h2>Underutilized rooms</h2>
                <p>{data.underutilized_definition}</p>
              </div>
            </div>
            <div className="underutilized-list">
              {data.underutilized_rooms.map(room => (
                <div className="underutilized-room" key={room.room_id}>
                  <div>
                    <strong>{room.room_name}</strong>
                    <small>{room.room_id} · {room.building} · capacity {room.capacity}</small>
                  </div>
                  <div className="underutilized-rate">
                    <strong>{room.teaching_utilization_percent}%</strong>
                    <small>{room.teaching_occupied_periods}/{room.teaching_available_periods} periods</small>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="panel analytics-panel analytics-room-table">
            <div className="analytics-panel-heading">
              <div>
                <h2>Room-by-room metrics</h2>
                <p>Teaching schedule and approved events shown independently</p>
              </div>
              <div className="analytics-room-filters">
                <label className="analytics-search">
                  <Search size={15} aria-hidden="true" />
                  <span className="visually-hidden">Search rooms</span>
                  <input
                    type="search"
                    value={search}
                    onChange={event => setSearch(event.target.value)}
                    placeholder="Search rooms"
                  />
                </label>
                <label>
                  <span className="visually-hidden">Filter room type</span>
                  <select value={roomType} onChange={event => setRoomType(event.target.value)}>
                    <option value="ALL">All types</option>
                    {data.by_room_type.map(item => (
                      <option key={item.room_type} value={item.room_type}>
                        {item.room_type.replaceAll("_", " ")}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
            <div className="analytics-table-scroll">
              <table className="analytics-room-table-grid">
                <thead>
                  <tr>
                    <th scope="col">Room</th>
                    <th scope="col">Type</th>
                    <th scope="col">Capacity</th>
                    <th scope="col">Teaching periods</th>
                    <th scope="col">Teaching utilization</th>
                    <th scope="col">Approved event periods</th>
                    <th scope="col">Event utilization</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRooms.map(room => (
                    <tr key={room.room_id}>
                      <th scope="row">
                        {room.room_name}
                        <small>{room.room_id} · {room.building}</small>
                      </th>
                      <td>{room.room_type.replaceAll("_", " ")}</td>
                      <td>{room.capacity}</td>
                      <td>{room.teaching_occupied_periods}/{room.teaching_available_periods}</td>
                      <td>
                        <strong>{room.teaching_utilization_percent}%</strong>
                        <UtilizationBar value={room.teaching_utilization_percent} label={`${room.room_id} teaching utilization`} />
                      </td>
                      <td>{room.event_occupied_periods}/{room.event_available_periods}</td>
                      <td>
                        <strong>{room.event_utilization_percent}%</strong>
                        <UtilizationBar value={room.event_utilization_percent} label={`${room.room_id} approved event utilization`} />
                      </td>
                    </tr>
                  ))}
                  {filteredRooms.length === 0 && (
                    <tr><td className="analytics-empty" colSpan="7">No rooms match these filters.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </section>
  );
}

export default AnalyticsPage;
