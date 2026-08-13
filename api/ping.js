// Temporary diagnostic. If /api/ping-js responds but /api/ping-py does not,
// the api/ directory is being routed and the problem is Python-specific.
// Delete once the deployment is confirmed working.
export default function handler(req, res) {
  res.status(200).json({ ok: true, runtime: "node" });
}
