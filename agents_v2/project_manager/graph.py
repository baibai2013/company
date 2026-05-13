from agents_v2.shared.smart_graph import build_smart_agent
from agents_v2.project_manager.prompts import SYSTEM_PROMPT, CC_PROMPT

def build_agent(checkpointer):
    return build_smart_agent(SYSTEM_PROMPT, checkpointer, cc_prompt=CC_PROMPT)
