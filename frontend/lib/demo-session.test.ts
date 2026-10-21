import { afterEach, describe, expect, it, vi } from "vitest";
import { newVisitor, readVisitor, visitorCookie } from "./demo-session";

afterEach(() => vi.useRealTimers());
describe("anonymous demo session", () => {
  const request = new Request("https://demo.example/api/demo/study-plans");
  it("uses signed, secure HttpOnly host-only cookies", () => {
    const visitor = newVisitor();
    const cookie = visitorCookie(request, "test-secret", visitor);
    expect(cookie).toContain("__Host-cp_demo_visitor=");
    expect(cookie).toContain("HttpOnly; SameSite=Strict; Secure");
    expect(cookie).not.toContain("Domain=");
    const incoming = new Request(request.url, { headers: { Cookie: cookie.split(";")[0] } });
    expect(readVisitor(incoming, "test-secret")).toEqual(visitor);
    expect(readVisitor(incoming, "wrong-secret")).toBeNull();
  });
  it("rejects modified and expired cookies", () => {
    vi.useFakeTimers();
    const cookie = visitorCookie(request, "test-secret", newVisitor()).split(";")[0];
    const forged = new Request(request.url, { headers: { Cookie: `${cookie.slice(0, -1)}x` } });
    expect(readVisitor(forged, "test-secret")).toBeNull();
    vi.advanceTimersByTime(31 * 86400 * 1000);
    expect(readVisitor(new Request(request.url, { headers: { Cookie: cookie } }), "test-secret")).toBeNull();
  });
});
