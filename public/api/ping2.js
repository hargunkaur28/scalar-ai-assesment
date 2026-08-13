// Diagnostic: only becomes a function if Vercel's Root Directory is `public`.
export default function handler(req, res) {
  res.status(200).json({ ok: true, rootDirectoryIs: "public" });
}
