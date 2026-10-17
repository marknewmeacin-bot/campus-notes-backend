import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=os.getenv("API_DEBUG", "").lower() in {"1", "true", "yes"},
    )
