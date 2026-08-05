# PantryChef 🍳

An AI-powered recipe generator that turns leftover ingredients into real,
cookable recipes — built with Flask and the OpenRouter API.

## What's new in this version

The first version was a single Flask route that shipped a prompt to one
model and dumped whatever Markdown came back into the page. This version
is a small but real application:

- **Structured recipes, not a text blob.** The model is asked for strict
  JSON matching a schema and the response is parsed and validated
  server-side, so each recipe renders as its own card with a working
  **Save** button — not one undifferentiated wall of text.
- **Automatic model fallback.** OpenRouter's free-model lineup rotates and
  any single model can be rate-limited at any moment. Instead of hardcoding
  model ids (which go stale), `ai_client.py` fetches the *current* list of
  zero-cost models from OpenRouter's public `/models` endpoint and retries
  through it automatically if one fails — no manual "try a different model"
  step needed anymore.
- **Response caching.** Re-submitting the same ingredients/diet/cuisine
  within 10 minutes returns the cached result instead of spending another
  request against the shared quota.
- **Favorites + star ratings**, backed by a real (if tiny) SQLite database.
  Save any generated recipe, rate it 1–5 stars, remove it later — all on a
  dedicated `/favorites` page.
- **A test suite** (`tests/test_app.py`) covering prompt construction, JSON
  parsing (including malformed/fenced responses), the model fallback and
  caching logic, the database CRUD functions, and the Flask routes — all
  mocked, so it runs offline in well under a second.

## Setup (one-time, ~5 minutes)

1. **Get a free OpenRouter API key** — https://openrouter.ai/keys
   (email or GitHub login, no card required).

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set your key once**, in a `.env` file:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and paste your real key in place of `your-key-here`.
   `.env` is already in `.gitignore`.

4. **Run the app**
   ```bash
   python app.py
   ```

5. Open **http://localhost:5000**. This is the *only* key needed — nobody
   using this instance has to bring their own.

## Running the tests

```bash
pytest
```
All tests mock the network layer, so nothing here actually calls
OpenRouter — they run instantly and don't touch your API quota.

## Project structure

```
app.py               Flask routes (generator + favorites API)
ai_client.py          OpenRouter integration: prompting, JSON parsing,
                       dynamic free-model fallback, response caching
db.py                 SQLite persistence for saved/rated recipes
templates/
  base.html            shared layout + nav
  index.html           recipe generator page
  favorites.html        saved recipes page
static/
  style.css            shared styling
  app.js               recipe card rendering, save/rate/delete handlers
tests/
  test_app.py           pytest suite (offline, mocked network)
.env.example           template for your local .env
requirements.txt
```

## Why one shared key instead of "bring your own"
This is a small local/demo tool — you're the only one running it, so the
key belongs on the server, set once, never touched again. The trade-off:
if you ever deploy this somewhere public (not just localhost), everyone
who visits shares your key and quota. Basic per-IP throttling (3 seconds
between requests) blunts accidental abuse, but this app isn't hardened
for public hosting as-is — keep it local unless you add real
authentication.

## About rate limits
Free OpenRouter models are shared and capped — typically ~20
requests/minute and 50/day with no credits added (1,000/day once you've
added $10+ in credits). The automatic model fallback means a single
rate-limited model no longer stops you; you'll only see an error if every
currently-free model is unavailable at once, which is rare.

## Notes
- Runs entirely on your own machine — nothing is deployed publicly unless
  you choose to do so.
- `pantrychef.db` (your favorites) is created automatically on first run
  and gitignored.
