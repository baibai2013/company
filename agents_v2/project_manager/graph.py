from agents_v2.shared.smart_graph import build_smart_agent
from agents_v2.project_manager.prompts import CC_PROMPT


def build_agent(checkpointer):
    return build_smart_agent("project_manager", checkpointer, cc_prompt=CC_PROMPT)
