import os

from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()  

APP_TITLE = "AI Business Intelligence Agent"
UPLOAD_DIR = "uploads"
# Reverted from "openai/gpt-oss-120b". That model is the last functional change
# this repo saw (c433f81, 29 Aug) and the endpoint has been failing ~8 calls in
# 10 since: it answers without the well-formed tool call that
# with_structured_output(method="function_calling") requires.
MODEL_NAME = "llama-3.3-70b-versatile"
TEMPERATURE = 0
# Groq's free tier rate-limits per minute; let the client ride out a 429
# rather than surfacing it as a failed request.
MAX_RETRIES = 4

os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_llm(
    model_name: str = MODEL_NAME,
    temperature: float = TEMPERATURE,
    max_retries: int = MAX_RETRIES,
) -> ChatGroq:
    """Create a ChatGroq LLM instance. Requires GROQ_API_KEY to already be set in the environment."""
    if not os.environ.get("GROQ_API_KEY"):
        raise EnvironmentError(
            "GROQ_API_KEY is not set. Add it to your .env file (see .env.example)."
        )
    return ChatGroq(model=model_name, temperature=temperature, max_retries=max_retries)


# python -m pip install -r requirements.txt
# python -m streamlit run main.py
