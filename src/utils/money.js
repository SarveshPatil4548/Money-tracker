export function toMinor(x) {
  const n = Number(x);
  if (!isFinite(n)) return 0;
  return Math.round(n * 100);
}

export function fromMinor(x) {
  const n = Number(x) || 0;
  return (n / 100).toFixed(2);
}

export function formatINRMinor(x) {
  const n = Number(x) || 0;
  const abs = Math.abs(n);
  const rupees = Math.floor(abs / 100);
  const paise = String(abs % 100).padStart(2, "0");
  let s = String(rupees);
  if (s.length > 3) {
    let head = s.slice(0, -3);
    const tail = s.slice(-3);
    const g = [];
    while (head) {
      g.push(head.slice(-2));
      head = head.slice(0, -2);
    }
    s = g.reverse().join(",") + "," + tail;
  }
  return (n < 0 ? "-" : "") + "₹" + s + "." + paise;
}
