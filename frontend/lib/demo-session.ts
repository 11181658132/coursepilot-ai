import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

const LIFETIME = 30 * 24 * 60 * 60;
type Visitor = { v: 1; id: string; exp: number };
export function digest(secret: string, value: string) {
  return createHmac("sha256", secret).update(value).digest("hex");
}
export function cookieName(request: Request) {
  return new URL(request.url).protocol === "https:" ? "__Host-cp_demo_visitor" : "cp_demo_visitor";
}
export function readVisitor(request: Request, secret: string): Visitor | null {
  const token = request.headers.get("cookie")?.split(";").map(item => item.trim())
    .find(item => item.startsWith(`${cookieName(request)}=`))?.slice(cookieName(request).length + 1);
  if (!token || token.length > 500) return null;
  const parts = token.split(".");
  if (parts.length !== 2 || !/^[a-f0-9]{64}$/.test(parts[1])) return null;
  const expected = Buffer.from(digest(secret, `visitor-v1.${parts[0]}`), "hex");
  if (!timingSafeEqual(expected, Buffer.from(parts[1], "hex"))) return null;
  try {
    const visitor = JSON.parse(Buffer.from(parts[0], "base64url").toString()) as Visitor;
    const now = Math.floor(Date.now() / 1000);
    return visitor.v === 1 && /^[a-f0-9]{64}$/.test(visitor.id)
      && Number.isInteger(visitor.exp) && visitor.exp > now && visitor.exp <= now + LIFETIME + 60
      ? visitor : null;
  } catch { return null; }
}
export function newVisitor() {
  return { v: 1 as const, id: randomBytes(32).toString("hex"), exp: Math.floor(Date.now() / 1000) + LIFETIME };
}
export function visitorCookie(request: Request, secret: string, visitor: Visitor) {
  const body = Buffer.from(JSON.stringify(visitor)).toString("base64url");
  const signature = digest(secret, `visitor-v1.${body}`);
  return `${cookieName(request)}=${body}.${signature}; Path=/; Max-Age=${LIFETIME}; HttpOnly; SameSite=Strict${new URL(request.url).protocol === "https:" ? "; Secure" : ""}`;
}
