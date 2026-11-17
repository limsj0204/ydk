// 앱 설치(홈 화면 추가)를 위한 최소한의 서비스 워커.
// 페이지는 항상 네트워크에서 새로 받고, 오프라인일 때만 마지막으로 받은 사본을 보여준다.
// 조회수 데이터(raw.githubusercontent.com 등 다른 주소)는 건드리지 않는다.
const CACHE = "ydk-shell-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin || req.url.includes("/data/")) return;
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match("./")))
  );
});
