"""Entry point for the Stock Scanner Bot.

Run from the stock_scanner/ directory:
    python run.py

Then open http://127.0.0.1:8000 in a browser.
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
