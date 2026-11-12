const ORIGIN = "https://weread-reading.pages.dev";

export default {
  async fetch(request) {
    const incoming = new URL(request.url);
    const target = new URL(incoming.pathname + incoming.search, ORIGIN);
    const upstream = new Request(target.toString(), request);
    const response = await fetch(upstream);
    const headers = new Headers(response.headers);
    headers.set("x-weread-edge", "cloudflare-worker");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  },
};
