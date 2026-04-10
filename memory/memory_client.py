import os
import logging
import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)

def enhance_prompt(instruction: str) -> str:
    """
    Sends an instruction to the memory service for enhancement.

    Args:
        instruction: The original prompt instruction.

    Returns:
        The enhanced prompt string, or the original instruction if enhancement fails.
    """
    base_url = os.environ.get("MEMORY_SERVICE_URL", "http://localhost:3000")
    url = f"{base_url}/bridge/enhance"
    
    payload = json.dumps({"prompt": instruction, "limit": 5}).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    
    req = Request(url, data=payload, headers=headers)
    
    try:
        with urlopen(req, timeout=3) as response:
            response_data = json.loads(response.read().decode('utf-8'))
            return response_data.get("enhancedPrompt", instruction)
    except (URLError, HTTPError, json.JSONDecodeError, ConnectionError) as e:
        logger.warning(f"Failed to enhance prompt for '{instruction}'. Error: {e}. Returning original instruction.")
        return instruction

def ingest_result(input_text: str, output_text: str, agent: str) -> None:
    """
    Ingests a result pair (input/output) into the memory service.

    Args:
        input_text: The input text.
        output_text: The output text.
        agent: The agent that produced the result.
    """
    base_url = os.environ.get("MEMORY_SERVICE_URL", "http://localhost:3000")
    url = f"{base_url}/bridge/ingest"
    
    payload = json.dumps({"input": input_text, "output": output_text, "agent": agent}).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    
    req = Request(url, data=payload, headers=headers)
    
    try:
        with urlopen(req, timeout=3) as response:
            # We log warning on any exception, never raise, so we just attempt the request.
            pass
    except (URLError, HTTPError, json.JSONDecodeError, ConnectionError) as e:
        logger.warning(f"Failed to ingest result for agent '{agent}'. Error: {e}.")
