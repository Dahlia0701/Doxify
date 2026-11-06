
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(api_key=os.getenv("LLM_API_KEY"))

try:
    response = client.models.generate_content(
        model=os.getenv("LLM_MODEL", "gemini-2.5-flash"),
        contents="Reply with only the word SUCCESS."
    )

    print("Response:", response.text)

except Exception as e:
    print("Error type:", type(e).__name__)
    print("Error details:", str(e))

