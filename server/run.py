"""启动入口：uvicorn app.main:app --port 8402"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8402)
