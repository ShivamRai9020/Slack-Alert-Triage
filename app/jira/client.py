"""
Thin wrapper over the Jira Cloud REST API v3. Kept deliberately minimal --
just the operations this project needs (create issue, comment, search by
label). Auth uses email + API token (Jira Cloud basic auth), read from
env vars only -- never hardcode or log the token.
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class JiraClient:
    def __init__(self):
        self.enabled = bool(settings.jira_base_url and settings.jira_email and settings.jira_api_token)
        if self.enabled:
            self._client = httpx.Client(
                base_url=settings.jira_base_url,
                auth=(settings.jira_email, settings.jira_api_token),
                headers={"Content-Type": "application/json"},
                timeout=10.0,
            )
        else:
            self._client = None
            logger.warning(
                "Jira credentials not configured -- running in dry-run mode "
                "(tickets will be logged, not created)."
            )

    def find_open_issue_by_label(self, label: str) -> dict | None:
        """Search for an existing non-done issue tagged with this label (the fingerprint hash)."""
        if not self.enabled:
            return None

        jql = f'labels = "{label}" AND statusCategory != Done ORDER BY created DESC'
        resp = self._client.get("/rest/api/3/search", params={"jql": jql, "maxResults": 1})
        resp.raise_for_status()
        issues = resp.json().get("issues", [])
        return issues[0] if issues else None

    def create_issue(
        self,
        summary: str,
        description: str,
        labels: list[str],
        issue_type: str | None = None,
        priority: str | None = None,
    ) -> dict:
        """Returns {'key': ..., 'url': ...}. In dry-run mode, returns a fake key and logs instead."""
        if not self.enabled:
            fake_key = f"DRYRUN-{abs(hash(summary)) % 10000}"
            logger.info("[DRY-RUN] Would create Jira issue %s: %s\n%s", fake_key, summary, description)
            return {"key": fake_key, "url": None}

        payload = {
            "fields": {
                "project": {"key": settings.jira_project_key},
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": description}]}
                    ],
                },
                "issuetype": {"name": issue_type or settings.jira_default_issue_type},
                "labels": labels,
            }
        }
        if priority:
            payload["fields"]["priority"] = {"name": priority}

        resp = self._client.post("/rest/api/3/issue", json=payload)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                "Jira issue creation failed: HTTP %s -- %s",
                resp.status_code,
                resp.text[:500],
            )
            raise
        data = resp.json()
        key = data["key"]
        url = f"{settings.jira_base_url}/browse/{key}"
        return {"key": key, "url": url}

    def add_comment(self, issue_key: str, comment_text: str) -> None:
        if not self.enabled:
            logger.info("[DRY-RUN] Would comment on %s: %s", issue_key, comment_text)
            return

        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": comment_text}]}],
            }
        }
        resp = self._client.post(f"/rest/api/3/issue/{issue_key}/comment", json=payload)
        resp.raise_for_status()

    def get_issue_status_category(self, issue_key: str) -> str | None:
        """
        Returns the issue's status category name ("To Do", "In Progress",
        "Done") or None on any failure/dry-run. Used by the dashboard's
        "sync" action to pull an incident's local `status` back in line
        with what Jira actually shows, in case someone resolved the
        ticket directly in Jira rather than through this app.
        """
        if not self.enabled:
            return None

        try:
            resp = self._client.get(f"/rest/api/3/issue/{issue_key}", params={"fields": "status"})
            resp.raise_for_status()
            data = resp.json()
            return data["fields"]["status"]["statusCategory"]["name"]
        except Exception:
            logger.warning("Could not fetch status for Jira issue %s", issue_key, exc_info=True)
            return None
