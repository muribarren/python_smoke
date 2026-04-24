import azure.functions as func
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import asyncio

app = FastAPI()

@app.get("/")
def root():
    return {"message": "hola Oswaldo!"}

async def main(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    from azure.functions._abc import TraceContext, RetryContext
    from azure.functions import AsgiMiddleware
    return await AsgiMiddleware(app).handle_async(req, context)
