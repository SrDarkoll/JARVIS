LLM_UNCONFIGURED_MESSAGE = "Configure GROQ_API_KEY to enable AI responses."
CHAT_UNAVAILABLE_MESSAGE = "The AI service is temporarily unavailable."


class LLMUnavailableError(RuntimeError):
    """Raised when a request needs an LLM but no provider is configured."""

    def __init__(self):
        super().__init__("No LLM provider is configured.")


class LLMServiceError(RuntimeError):
    """Raised when a configured LLM cannot complete a request."""

    def __init__(self):
        super().__init__("The LLM service is temporarily unavailable.")
