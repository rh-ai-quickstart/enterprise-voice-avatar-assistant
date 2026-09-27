/** "12 min", "1 h 5 min", "2 d 3 h". */
export function duration(minutes: number): string {
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const rest = Math.round(minutes % 60);
    return rest ? `${hours} h ${rest} min` : `${hours} h`;
  }
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days} d ${hours % 24} h` : `${days} d`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** pending_approval -> pending approval */
export function words(value: string | null | undefined): string {
  return value ? value.replace(/_/g, " ") : "-";
}

/** A date input's day (YYYY-MM-DD, local time) as the start of that day for the API. */
export function dayStart(day: string): string {
  return new Date(`${day}T00:00:00`).toISOString();
}

/** The start of the next day, so a "to" date includes the whole day. */
export function dayAfter(day: string): string {
  const next = new Date(`${day}T00:00:00`);
  next.setDate(next.getDate() + 1);
  return next.toISOString();
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : `${Math.round(value * 100)}%`;
}

export function bytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
  return `${(size / 1024 / 1024).toFixed(1)} MiB`;
}
