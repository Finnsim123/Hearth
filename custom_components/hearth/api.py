"""Thin async client for the Hearth backend API (bearer-token scope)."""
from __future__ import annotations

from typing import Any

import aiohttp


class HearthApiError(Exception):
    """Backend unreachable or returned an error."""


class HearthAuthError(HearthApiError):
    """Token invalid, revoked, or out of scope."""


class HearthClient:
    def __init__(self, host: str, token: str, session: aiohttp.ClientSession) -> None:
        self._base = host.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._session = session

    async def _get(self, path: str) -> Any:
        try:
            async with self._session.get(
                f"{self._base}{path}", headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (401, 403):
                    raise HearthAuthError(f"{resp.status} on {path}")
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise HearthApiError(str(exc)) from exc

    async def validate(self) -> bool:
        """True when host is a Hearth and the token has integration scope."""
        await self._get("/api/persons")
        return True

    async def persons(self) -> list[dict]:
        return await self._get("/api/persons")

    async def latest_predictions(self) -> dict[str, dict]:
        """{person_id: latest prediction dict} — newest-first lists collapsed."""
        data = await self._get("/api/predictions?hours=2")
        out: dict[str, dict] = {}
        for pid, preds in (data.get("persons") or {}).items():
            if preds:
                out[pid] = preds[0]
        return out

    async def post_action(self, action: str, device: str | None) -> bool:
        return await self._post("/api/feedback/action",
                                {"action": action, "device": device}) is not None

    async def _post(self, path: str, payload: dict) -> Any:
        try:
            async with self._session.post(
                f"{self._base}{path}", headers=self._headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (401, 403):
                    raise HearthAuthError(f"{resp.status} on {path}")
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise HearthApiError(str(exc)) from exc

    # ── model diagnostics (accuracy sensor + attention binary_sensor) ───────
    async def models(self) -> list[dict]:
        """The model registry — the live (promoted) root model per person carries
        the honest accuracy metrics."""
        return await self._get("/api/models")

    async def cadence(self) -> dict:
        """{person_id: {phase, interval_days, …}} — the adaptive training cadence."""
        return await self._get("/api/models/cadence")

    async def advisories(self) -> list[dict]:
        """Active, non-dismissed advisories (things Hearth wants you to know)."""
        data = await self._get("/api/advisories")
        return data.get("advisories") or []

    # ── two-way controls (override + questions opt-out) ─────────────────────
    async def controls(self) -> dict:
        """{"activities": [slug…], "persons": {pid: {override, questions}}}."""
        return await self._get("/api/controls")

    async def set_override(self, person_id: str, activity: str) -> None:
        await self._post(f"/api/persons/{person_id}/override", {"activity": activity})

    async def set_questions(self, person_id: str, enabled: bool) -> None:
        await self._post(f"/api/persons/{person_id}/questions", {"enabled": bool(enabled)})
