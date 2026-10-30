from enum import Enum


class Worker(Enum):
    """The workers of the response engine that call this service, each with keys of its own (ADR-0022)."""
    CLASSIFIER = "classifier"    # Guardrails: reads the rules of a chatbot
    COORDINATOR = "coordinator"  # Reads the rules, the tools and the last turns of a conversation
    LINK = "link"                # MCP and external APIs: reads the tools
    RESPONDER = "responder"      # Writes the answer: reads the rules
    EVALUATOR = "evaluator"      # Tone and certainty: reads the rules
    NOTIFIER = "notifier"        # Sends the answer to Meta: the only one that gets the Meta tokens
