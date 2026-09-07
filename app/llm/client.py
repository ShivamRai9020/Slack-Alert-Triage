"""
Thin wrapper over the Ollama API (via httpx) for free local LLM generation.
Every call degrades to None on any failure (missing service, timeout, network error) 
rather than raising -- callers treat None as "fall back to the deterministic output,"
never as an error to propagate. This is the enforcement point for the
"system stays reliable if the LLM is unavailable" requirement.
"""
import logging
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self):
        # Only enable if the composer is turned on in settings
        # We no longer check for an API key since Ollama runs locally for free!
        self.enabled = getattr(settings, "enable_llm_composer", False)
        
        # Default Ollama port
        self.api_url = getattr(settings, "ollama_base_url", "http://localhost:11434") + "/api/chat"

        if self.enabled:
            try:
                import httpx
            except ImportError:
                logger.warning(
                    "ENABLE_LLM_COMPOSER is set but the httpx client could not be "
                    "imported. LLM features disabled, falling back to deterministic "
                    "output only.",
                    exc_info=True,
                )
                self.enabled = False

    def complete(self, system: str, user: str, max_tokens: int = 200) -> str | None:
        """Returns the model's text response, or None on any failure."""
        if not self.enabled:
            return None

        # Build the payload according to Ollama's API specs
        payload = {
            "model": getattr(settings, "llm_model", "llama3.2"),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            "stream": False,
            "options": {
                "num_predict": max_tokens
            }
        }

        try:
            # Use httpx to call the local Ollama instance
            timeout = getattr(settings, "llm_timeout_seconds", 30)
            with httpx.Client(timeout=timeout) as client:
                response = client.post(self.api_url, json=payload)
                response.raise_for_status()
                
                data = response.json()
                text = data.get("message", {}).get("content", "")
                
                return text.strip() or None
                
        except httpx.ConnectError:
            logger.warning("Could not connect to Ollama. Is it running? Falling back to deterministic output.")
            return None
        except Exception:
            logger.warning("LLM call failed -- falling back to deterministic output.", exc_info=True)
            return None
