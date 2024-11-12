from modal import Image, App, enter, method, asgi_app, gpu
from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette import EventSourceResponse
from typing import List

MODEL_DIR = "/model"
MODEL_FILENAME = "mistral-7b-instruct-v0.2.Q8_0.gguf"
MODEL_REPOS = "TheBloke/Mistral-7B-Instruct-v0.2-GGUF"

incontext = ""

web_app = FastAPI()

CORS_ORIGIN_WHITELIST = ['https://chitchatsource.com', 'https://www.chitchatsource.com']

web_app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGIN_WHITELIST,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

def download_model():
    from huggingface_hub import hf_hub_download
    hf_hub_download(repo_id=MODEL_REPOS, filename=MODEL_FILENAME, local_dir=MODEL_DIR)

image = (
    Image.from_registry("nvidia/cuda:12.2.0-devel-ubuntu22.04", add_python="3.10")
    .apt_install("build-essential", "clang")  # Install Clang
    .pip_install(
        "huggingface_hub",
        "sse_starlette",
    ).run_commands(
        "export CC=clang",  # Set the CC environment variable to clang
        "CMAKE_ARGS=\"-DLLAMA_CUBLAS=on\" FORCE_CMAKE=1 pip install llama-cpp-python==0.2.64 --force-reinstall --upgrade --no-cache-dir", gpu=gpu.T4())
    .run_function(download_model)
)

app = App("chitchat-gpu", image=image)

@app.cls(gpu=gpu.T4(count=1))
class llamacpp:
    @enter()
    def load_model(self):
        from llama_cpp import Llama
        self.llama = Llama(MODEL_DIR + "/" + MODEL_FILENAME, n_ctx=4096, n_gpu_layers=-1, verbose=True)
    @method(is_generator=True)
    def predict(self, question: str, context: str):

        formatted_question = f"{context}<s>[INST]{question}[/INST]"
        
        return self.llama(
        formatted_question,
        temperature=0.1,
        max_tokens=-1,
        stop=["</s>"],
        stream=True,
        echo=True
        )

@app.function()
@web_app.get("/llama")
async def handle_llama_query(request: Request, question: str, context: List[str] = Query(None)):

    formatted_context = ""

    if context and len(context) % 2 == 0:        
        for i in range(0, len(context), 2):
            formatted_context += f"<s>[INST]{context[i]}[/INST]{context[i+1]}</s>"

    stream = llamacpp().predict.remote_gen(question, formatted_context)

    async def stream_responses():
        for item in stream:
            if await request.is_disconnected():
                break
            response_text = item["choices"][0]["text"]
            yield {"data": response_text}

    return EventSourceResponse(stream_responses())

@app.function()
@asgi_app()
def entrypoint():
    return web_app