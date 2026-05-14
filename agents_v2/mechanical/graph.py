from agents_v2.shared.smart_graph import build_smart_agent

def build_agent(checkpointer):
    return build_smart_agent("mechanical", checkpointer)
