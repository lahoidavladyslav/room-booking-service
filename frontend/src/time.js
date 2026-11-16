// Europe/Kyiv helpers. No hardcoded +02:00/+03:00: the offset comes from Intl (DST-safe).
const TZ = "Europe/Kyiv";

const parts = (date) =>
  Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: TZ, hourCycle: "h23",
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }).formatToParts(date).map((p) => [p.type, p.value]),
  );

/** "HH:MM" of an instant, in Kyiv time. */
export function kyivHHMM(iso) {
  const p = parts(new Date(iso));
  return `${p.hour}:${p.minute}`;
}

/** Offset (minutes east of UTC) of Kyiv at the given instant. */
function offsetMinutes(date) {
  const p = parts(date);
  const asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute);
  return Math.round((asUtc - Math.floor(date.getTime() / 60000) * 60000) / 60000);
}

/** ISO 8601 string with Kyiv offset for wall-clock `date` ("YYYY-MM-DD") + `hhmm` ("HH:MM"). */
export function kyivISO(date, hhmm) {
  const [y, mo, d] = date.split("-").map(Number);
  const [h, mi] = hhmm.split(":").map(Number);
  const wall = Date.UTC(y, mo - 1, d, h, mi);
  // two passes handle the DST boundary correctly
  let off = offsetMinutes(new Date(wall));
  off = offsetMinutes(new Date(wall - off * 60000));
  const sign = off >= 0 ? "+" : "-";
  const a = Math.abs(off);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date}T${pad(h)}:${pad(mi)}:00${sign}${pad(Math.floor(a / 60))}:${pad(a % 60)}`;
}

export const addMinutes = (hhmm, mins) => {
  const [h, m] = hhmm.split(":").map(Number);
  const t = h * 60 + m + mins;
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
};

export const toMinutes = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};

export const isWeekend = (date) => {
  const [y, m, d] = date.split("-").map(Number);
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return wd === 0 || wd === 6;
};

/** Today's date in Kyiv as YYYY-MM-DD. */
export const todayKyiv = () => {
  const p = parts(new Date());
  return `${p.year}-${p.month}-${p.day}`;
};
