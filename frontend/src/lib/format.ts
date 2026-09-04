/** Formatting helpers shared across every screen.
 *
 *  These live in one place because inconsistent number formatting is what made the four
 *  screens read as four products: the same rate appeared as `99.5%` on one and `0.99458`
 *  on another. UI/UX §3.1 fixes the rules — two decimals, right-aligned, Indian digit
 *  grouping for money — so they belong in a module, not re-derived per view.
 */

/** Em dash. The single representation of "we do not have this value", used everywhere so a
 *  missing figure can never be mistaken for a zero. */
export const NO_VALUE = '—'

/** `0.99458` -> `99.46%`. Rates arrive from the API as fractions, never as percentages. */
export function pct(value: number | null | undefined, decimals = 2): string {
  if (value == null || Number.isNaN(value)) return NO_VALUE
  return `${(value * 100).toFixed(decimals)}%`
}

/** Percentage-point difference between two fractions, signed. `+5.11 pp`. */
export function deltaPp(a: number | null | undefined, b: number | null | undefined): string | null {
  if (a == null || b == null) return null
  const pp = (a - b) * 100
  return `${pp >= 0 ? '+' : ''}${pp.toFixed(2)} pp`
}

/** Indian digit grouping with exactly two decimals: `1234567.8` -> `12,34,567.80`.
 *  Money crosses the API as a decimal *string* so no precision is lost in transit; it is
 *  parsed here only to insert separators, never to compute with. */
export function money(value: string | number | null | undefined): string {
  if (value == null || value === '') return NO_VALUE
  const num = typeof value === 'string' ? Number.parseFloat(value) : value
  if (Number.isNaN(num)) return NO_VALUE

  const [whole, decimals] = Math.abs(num).toFixed(2).split('.')
  let lastThree = whole.slice(-3)
  const rest = whole.slice(0, -3)
  if (rest !== '') lastThree = `,${lastThree}`
  const grouped = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',') + lastThree
  return `${num < 0 ? '-' : ''}${grouped}.${decimals}`
}

/** Money with the rupee sign attached — for prose and confirmation dialogs, where the
 *  currency must be unambiguous. Table cells use `<MoneyCell>` instead. */
export function rupees(value: string | number | null | undefined): string {
  const formatted = money(value)
  return formatted === NO_VALUE ? NO_VALUE : `₹${formatted}`
}

/** Plain integer counts, grouped in threes (these are quantities, not amounts). */
export function count(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return NO_VALUE
  return value.toLocaleString('en-US')
}

/** `2026-01-01` + `2026-01-31` -> `2026-01-01 – 2026-01-31`. ISO dates are left as-is
 *  deliberately: they sort, they are unambiguous, and this is a tool for an analyst. */
export function period(start: string, end: string): string {
  return `${start} – ${end}`
}

/** `2026-09-04T11:22:33.123456+00:00` -> `2026-09-04 11:22`. */
export function timestamp(iso: string | null | undefined): string {
  if (!iso) return NO_VALUE
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** `missing_in_bank` -> `Missing in bank`. The fallback for taxonomy values that have no
 *  hand-written label; a raw snake_case token in the UI reads as a leaked internal. */
export function humanise(token: string): string {
  const spaced = token.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}
