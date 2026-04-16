import os
import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class MemoryClient:
    def __init__(self, memory_url: Optional[str] = None):
        # Default to environment variable if not passed
        self.base_url = memory_url or os.getenv("BRIDGE_MEMORY_URL")
        # Ensure it doesn't end with a trailing slash
        if self.base_url and self.base_url.endswith("/"):
            self.base_url = self.base_url[:-1]

    @property
    def is_enabled(self) -> bool:
        return bool(self.base_url)

    def enhance_prompt(self, prompt: str, agent_name: Optional[str] = "bridge", limit: int = 5) -> str:
        """
        Calls the Memory Service to inject historical context into the prompt.
        If the service fails or is offline, it safely falls back to returning the original prompt.
        """
        if not self.is_enabled:
            return prompt

        try:
            url = f"{self.base_url}/bridge/enhance"
            payload = {
                "prompt": prompt,
                "agentName": agent_name,
                "limit": limit
            }
            # Short timeout to avoid blocking execution for too long
            response = requests.post(url, json=payload, timeout=5.0)
            response.raise_for_status()
            
            data = response.json()
            enhanced_prompt = data.get("enhancedPrompt")
            if enhanced_prompt:
                logger.debug("Successfully enhanced prompt with memory context.")
                return enhanced_prompt
            
        except requests.exceptions.RequestException as e:
            logger.warning("Memory Service (/bridge/enhance) unreachable or failed: %s. Falling back to original prompt.", e)
        except Exception as e:
            logger.error("Unexpected error parsing memory service response: %s", e)

        # Graceful fallback
        return prompt

    def ingest_event(self, input_prompt: str, output_code: str, agent_name: Optional[str] = "bridge") -> None:
        """
        Fires an ingestion request to store the outcome of a successful Aider task.
        """
        if not self.is_enabled:
            return

        try:
            url = f"{self.base_url}/bridge/ingest"
            payload = {
                "input": input_prompt,
                "output": output_code,
                "agent": agent_name
            }
            # Short timeout, fire and forget style
            response = requests.post(url, json=payload, timeout=3.0)
            response.raise_for_status()
            logger.debug("Successfully ingested task result into Memory Service.")
            
        except requests.exceptions.RequestException as e:
            logger.warning("Memory Service (/bridge/ingest) failed to ingest task: %s", e)
        except Exception as e:
            logger.error("Unexpected error during memory ingestion: %s", e)

# Singleton-like instance for ease of use across the bridge
memory_client = MemoryClient()
