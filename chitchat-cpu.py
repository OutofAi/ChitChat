from modal import Image, Stub, method, asgi_app
from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette import EventSourceResponse
from typing import List

MODEL_DIR = "/model"
MODEL_FILENAME = "mistral-7b-instruct-v0.2.Q8_0.gguf"
MODEL_REPOS = "TheBloke/Mistral-7B-Instruct-v0.2-GGUF"

incontext = ""

web_app = FastAPI()

web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def download_model():
    from huggingface_hub import hf_hub_download
    hf_hub_download(repo_id=MODEL_REPOS, filename=MODEL_FILENAME, local_dir=MODEL_DIR)

image = (
    Image.from_registry("ubuntu:22.04", add_python="3.10")
    .apt_install("build-essential")
    .pip_install(
        "llama-cpp-python",
        "huggingface_hub",
        "sse_starlette",
    )
    .run_function(download_model)
)

stub = Stub("chitchat-cpu", image=image)

@stub.cls(cpu=1)
class llamacpp:
    def __enter__(self):
        from llama_cpp import Llama
        self.llama = Llama(MODEL_DIR + "/" + MODEL_FILENAME, n_ctx=4096)
    @method(is_generator=True)
    def predict(self, question: str, context: str):

        formatted_question = f"<s>{context}[INST]{question}[/INST][INST]"
        
        return self.llama(
        formatted_question,
        temperature=0.1,
        max_tokens=-1,
        stop=["[/INST]"],
        stream=True,
        echo=True
        )

@stub.function()
@web_app.get("/llama")
async def handle_llama_query(request: Request, question: str, context: List[str] = Query(None)):
    
    formatted_context = ""

    if context and len(context) % 2 == 0:        
        for i in range(0, len(context), 2):
            formatted_context += f"[INST]{context[i]}[/INST][INST]{context[i+1]}[/INST]"

    stream = llamacpp().predict.remote_gen(question, formatted_context)

    async def stream_responses():
        for item in stream:
            if await request.is_disconnected():
                break
            response_text = item["choices"][0]["text"]
            yield {"data": response_text}

    return EventSourceResponse(stream_responses())

@stub.function()
@asgi_app()
def entrypoint():
    return web_app