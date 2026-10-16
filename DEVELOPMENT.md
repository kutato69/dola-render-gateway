# Video Generation Gateway - Architecture & Technical Notes

Technical reference for the service architecture, profile coordination, and task pipeline.

---

## 1. Core Architecture

The system operates across three tiers:
1. **API Tier (`server.py`)**: FastAPI application exposing standard OpenAI video endpoints (`/v1/videos/generations`, `/v1/videos/<id>`), image endpoints (`/v1/images/generations`, `/v1/images/<id>`), and web dashboard routes (`/web`, `/api/admin/*`). Image tasks (`img_*`) run on the cookie-based `DolaPool` (`dola_client.py`, chatbot image ability `ability_type=16`, accounts from `cookies.txt`) — no browser automation; video tasks keep using `BrowserPool` + UI automation.
2. **Pool Management Tier (`browser_pool.py`)**: Manages persistent browser profiles in `accounts/`, controlling task concurrency, account locking, and daily quota rotations.
3. **Execution Tier (`video_worker_ui.py`, `video_worker.py`)**:
   - **UI Automation Mode**: Automates browser interactions, authenticates via persistent browser session, injects prompts, and handles verification challenges.
   - **Protocol Mode**: Sends structured SSE requests to completion endpoints and polls status endpoints for video rendering progress.

---

## 2. Duration Configuration

- The browser module manages duration parameters (`15s`, `30s`) via request interception.
- Synchronizes with client action bar configuration to present extended duration selections.

---

## 3. High-Definition Stream Processing

- Video rendering status is monitored through the event stream.
- High-definition stream URLs are parsed and downloaded directly to the local storage directory `downloads/`.

---

## 4. Verification Handling

- Handles verification challenges using automated visual template alignment to ensure reliable background execution.
