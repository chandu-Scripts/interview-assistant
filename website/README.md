# Personal download website

A static, responsive download hub inspired by LockedIn AI's navy/cyan palette, using our own
branding and an actual desktop-app preview. No accounts, analytics, personal documents, API
keys or browser-side secrets are included.

The live site still serves the previously verified v0.1.0 installer. Local website source has
draft v0.1.1 glass-theme release content, but **do not deploy it yet**: Windows Application
Control blocked the new unsigned executable (WinError 4551). Signing and a successful packaged
startup check are required before replacing the working download. `website/dist` remains the
previous v0.1.0 deployment payload.

## Build and preview

From the repository root:

```powershell
node website/build.mjs
node website/preview.mjs
```

Open `http://127.0.0.1:4173`. Node 20 or later is sufficient; there are no runtime dependencies.
The build reads the app version, copies the matching installer from `dist/installer/`, and
generates its size, date, release manifest and SHA-256 checksum. It fails if the installer is
missing, rather than publishing a broken download link. `website/dist/` is generated and ignored.
The published v0.1.0 refers to commit `61d6957` and 655 passing tests. The local v0.1.1 draft
has 658 passing tests and is an uncommitted working-tree build (`sourceCommit: null`). Update
verification metadata after signing and testing the packaged executable.

## Vercel deployment

This project uses Vercel because it was the hosting choice discussed with the owner. It does
not create or publish a separate Sites-hosted project.

1. Build locally, so the ignored Windows installer is included in the deployment directory.
2. Sign into the official CLI: `npx vercel login` (approve the browser/device prompt yourself).
3. From `website/dist`, run `npx vercel` and create/link the intended personal project.
4. Keep Vercel Authentication enabled for the resulting preview URL. Check access in a private
   browser window before sharing or using it as your personal download address.
5. Only promote to a production URL after configuring the intended production access policy.
   `noindex` headers prevent indexing requests; they are **not authentication**.

Deploy **only `website/dist`**, never the entire desktop repository. The prepared upload is
approximately 88.6 MB, below the documented 100 MB Hobby CLI-upload limit. If a later installer
exceeds the limit, move the binary to appropriately protected artifact storage. Do not put
private storage tokens into HTML or JavaScript.

Git-import deployment cannot access an installer that exists only on this laptop. This first
version deliberately uses local build plus CLI upload; a later release pipeline can automate it.
Production: https://chandoo-assistant.vercel.app (project `chandoo-assistant`, scope
`chandus-projects-889390b5`). Vercel Authentication is enabled for all deployments, including
production. Sign in with the owner's Vercel account to access the page and installer.
To update, build locally and run `npx vercel deploy --prod --yes` from `website/dist`.

## Verification performed

- Headless Edge: desktop at 1440px and mobile at 390px; no horizontal overflow or browser errors.
- Navigation selection, expanded setup note and checksum details, clipboard feedback.
- Actual download: 88,481,606 bytes, SHA-256 matched the release manifest.
- Installer remains an unsigned development build; the page states this accurately.
- Setup explains that new installation does not sync resumes/history/settings or API keys.

Local screenshots and the browser check are in `tmp/website-qa/` (not shipped).
Published on 2026-10-25 after owner authentication. The production homepage exactly matched
the local build. The downloaded production installer was 88,481,606 bytes and its SHA-256
matched the release manifest. An anonymous installer request returned HTTP 302 to sign-in.
No environment files are included in the deployment.
