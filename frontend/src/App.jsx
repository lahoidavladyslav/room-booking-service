import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import { addMinutes, isWeekend, kyivHHMM, kyivISO, toMinutes, todayKyiv } from "./time.js";

const OPEN = "08:00";
const CLOSE = "20:00";
const STEP = 15;
const SLOTS = Array.from({ length: (toMinutes(CLOSE) - toMinutes(OPEN)) / STEP }, (_, i) =>
  addMinutes(OPEN, i * STEP),
);
const DURATIONS = Array.from({ length: 16 }, (_, i) => (i + 1) * STEP); // 15 min .. 4 h

const fmtDur = (m) => (m % 60 === 0 ? `${m / 60} год` : m > 60 ? `${Math.floor(m / 60)} год ${m % 60} хв` : `${m} хв`);

export default function App() {
  const [user, setUser] = useState(() => localStorage.getItem("userId") || "alice");
  const [rooms, setRooms] = useState([]);
  const [roomId, setRoomId] = useState("");
  const [date, setDate] = useState(todayKyiv());
  const [bookings, setBookings] = useState([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState(null); // { start, duration, title }
  const [busy, setBusy] = useState(false);

  useEffect(() => localStorage.setItem("userId", user), [user]);

  const run = useCallback(async (fn) => {
    setError("");
    try { return await fn(); } catch (e) { setError(e.message); }
  }, []);

  useEffect(() => {
    if (!user.trim()) return;
    run(async () => {
      const list = await api("/rooms", { user });
      setRooms(list);
      setRoomId((cur) => cur || (list[0] ? String(list[0].id) : ""));
    });
  }, [user, run]);

  const load = useCallback(async () => {
    if (!roomId || !date || !user.trim()) return;
    await run(async () => {
      const q = new URLSearchParams({
        roomId, from: kyivISO(date, "00:00"), to: kyivISO(date, "23:45"), limit: "100",
      });
      setBookings((await api(`/bookings?${q}`, { user })).items);
    });
  }, [roomId, date, user, run]);

  useEffect(() => { setForm(null); load(); }, [load]);

  // map slot -> booking that covers it
  const slotMap = useMemo(() => {
    const map = {};
    for (const b of bookings) {
      const s = toMinutes(kyivHHMM(b.startAt));
      const e = toMinutes(kyivHHMM(b.endAt));
      for (const slot of SLOTS) {
        const t = toMinutes(slot);
        if (t >= s && t < e) map[slot] = b;
      }
    }
    return map;
  }, [bookings]);

  const weekend = isWeekend(date);

  // longest duration available from the selected start slot (until next booking / closing)
  const maxFree = (start) => {
    let n = 0;
    for (let t = toMinutes(start); t < toMinutes(CLOSE) && !slotMap[addMinutes("00:00", t)]; t += STEP) n += STEP;
    return n;
  };

  const pick = (slot) => {
    const free = maxFree(slot);
    setForm({ start: slot, duration: Math.min(60, free, 240), title: "" });
  };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    await run(async () => {
      await api("/bookings", {
        user, method: "POST",
        body: {
          roomId: Number(roomId),
          title: form.title,
          startAt: kyivISO(date, form.start),
          endAt: kyivISO(date, addMinutes(form.start, form.duration)),
        },
      });
      setForm(null);
    });
    setBusy(false);
    load();
  };

  const cancel = async (b) => {
    if (!confirm(`Скасувати «${b.title}»?`)) return;
    await run(() => api(`/bookings/${b.id}`, { user, method: "DELETE" }));
    load();
  };

  const room = rooms.find((r) => String(r.id) === roomId);
  const allowed = form ? DURATIONS.filter((d) => d <= maxFree(form.start)) : [];

  return (
    <main>
      <h1>Бронювання переговорних</h1>

      <div className="toolbar">
        <label>Користувач
          <input value={user} onChange={(e) => setUser(e.target.value)} />
        </label>
        <label>Кімната
          <select value={roomId} onChange={(e) => setRoomId(e.target.value)}>
            {rooms.map((r) => <option key={r.id} value={r.id}>{r.name} ({r.capacity} місць)</option>)}
          </select>
        </label>
        <label>Дата
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
      </div>

      {error && <div className="error" role="alert">{error}</div>}
      {!user.trim() && <div className="error">Вкажіть користувача (X-User-Id)</div>}
      {rooms.length === 0 && user.trim() && <p>Кімнат немає. Створіть їх через API або seed.</p>}

      {weekend ? (
        <p className="muted">Вихідний — бронювання недоступне (пн–пт, 08:00–20:00 за Києвом).</p>
      ) : (
        <div className="layout">
          <div className="grid" aria-label={`Сітка дня, ${room?.name ?? ""}`}>
            {SLOTS.map((slot) => {
              const b = slotMap[slot];
              const starts = b && kyivHHMM(b.startAt) === slot;
              const mine = b && b.userId === user;
              return (
                <div key={slot} className={`row ${b ? (mine ? "mine" : "busy") : "free"} ${slot.endsWith("00") ? "hour" : ""}`}>
                  <span className="time">{slot.endsWith("00") || slot.endsWith("30") ? slot : ""}</span>
                  {b ? (
                    <div className="cell">
                      {starts && (
                        <>
                          <strong>{b.title}</strong>
                          <small> · {b.userId} · {kyivHHMM(b.startAt)}–{kyivHHMM(b.endAt)}</small>
                          {mine && <button className="link" onClick={() => cancel(b)}>скасувати</button>}
                        </>
                      )}
                    </div>
                  ) : (
                    <button className="cell free-btn" onClick={() => pick(slot)} aria-label={`Вільно ${slot}`} />
                  )}
                </div>
              );
            })}
          </div>

          {form && (
            <form className="panel" onSubmit={submit}>
              <h2>Нове бронювання</h2>
              <p>{room?.name}, {date}</p>
              <label>Початок
                <select value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value, duration: 15 })}>
                  {SLOTS.filter((s) => !slotMap[s]).map((s) => <option key={s}>{s}</option>)}
                </select>
              </label>
              <label>Тривалість
                <select value={form.duration} onChange={(e) => setForm({ ...form, duration: Number(e.target.value) })}>
                  {allowed.map((d) => <option key={d} value={d}>{fmtDur(d)}</option>)}
                </select>
              </label>
              <p className="muted">до {addMinutes(form.start, form.duration)}</p>
              <label>Назва
                <input required maxLength={200} value={form.title} autoFocus
                  onChange={(e) => setForm({ ...form, title: e.target.value })} />
              </label>
              <div className="actions">
                <button type="submit" disabled={busy || !form.title.trim()}>Забронювати</button>
                <button type="button" className="secondary" onClick={() => setForm(null)}>Закрити</button>
              </div>
            </form>
          )}
        </div>
      )}
    </main>
  );
}
