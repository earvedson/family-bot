---
name: deploy-pi
description: Deploy or update family-bot on the production Raspberry Pi (git pull on the Pi, apply .env changes, handle pending deploy steps).
---

# Deploying to the Raspberry Pi

Production runs via cron on a Raspberry Pi on the home network (setup: `RASPBERRY_PI.md`). Its
host/user are deliberately not in this repo (it's public) — never add them here. They're in the
maintainer's Obsidian vault note `~/Obsidian/Personal/2. Areas/family-bot.md`, which also lists any
**pending deploy steps** left by a session on another machine.

1. Read the Obsidian note for host/user and pending steps.
2. Push to `main`.
3. `git pull` in the project dir on the Pi.
4. Apply any `.env` changes there by hand (`.env` isn't in git).
5. Tick off/remove the pending steps in the Obsidian note once done.

The Pi is only reachable from the home network.
