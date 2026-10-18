from openai import AsyncOpenAI


def create_openai_client(
    *,
    api_key: str,
    base_url: str | None = None,
    timeout: float = 300.0,
    max_retries: int = 0,
) -> AsyncOpenAI:
    """
    Create an OpenAI client with the given API keys and optional base URL.

    Args:
        api_key (str): The API key to use for authentication.
        base_url (str | None): Optional base URL for the OpenAI API.
        timeout (float): Timeout for requests in seconds. Default is 60.0.
        max_retries (int): Maximum number of retries for failed requests. Default is 2.

    Returns:
        AsyncOpenAI: An instance of the AsyncOpenAI client.
    """
    return AsyncOpenAI(
        api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries
    )
