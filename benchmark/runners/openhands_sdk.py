"""Minimal OpenHands SDK runner for the first benchmark candidate."""
import os
import sys

from openhands.sdk import Agent, Conversation, LLM
from openhands.tools.preset.default import get_default_tools


def main():
    model = os.environ["LLM_MODEL"]
    key = os.environ["GEMINI_API_KEY"]
    llm = LLM(
        model=model,
        api_key=key,
        usage_id="benchmark-openhands",
        api_mode="chat",
        drop_params=True,
        # Retry transient provider failures (such as Gemini HTTP 503) inside\n        # the same conversation, without replaying completed agent steps.\n        num_retries=5,\n        retry_multiplier=2,\n        retry_min_wait=20,\n        retry_max_wait=120,
        capability_overrides={
            "supports_prompt_cache": False,
            "supports_prompt_cache_key": False,
        },
    )
    tools = [t for t in get_default_tools(enable_browser=False) if t.name != "task_tracker"]
    agent = Agent(llm=llm, tools=tools, system_prompt_kwargs={"cli_mode": True})
    conversation = Conversation(
        agent=agent,
        workspace=os.getcwd(),
        max_iteration_per_run=15,
        persistence_dir="/tmp/benchmark-openhands-state",
        delete_on_close=False,
    )
    prompt = sys.stdin.read()
    if not prompt.strip():
        raise ValueError("Missing benchmark issue prompt")
    conversation.send_message(prompt)
    conversation.run()
    print("OpenHands run finished; inspect git diff and tests for actual success.")


if __name__ == "__main__":
    main()
