# Relay server

Cloudflare Workers, TypeScript. Planned for stage 0.5.

Forwards requests from signed-in users to the model provider so the provider
key never ships with the app.

Requirements:

- Forward only. No conversation content is stored or logged, including in
  error logs.
- Per-user authentication with short-lived tokens.
- Per-account quota. Only usage counts are recorded.
- Streaming passthrough.
- Versioned API, independent of the client platform.
