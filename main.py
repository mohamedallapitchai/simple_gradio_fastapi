import asyncio
import os
import time
from urllib.parse import urlunparse, urlparse

import gradio as gr
import uvicorn
from authlib.integrations.starlette_client import OAuth, OAuthError
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Request
from langgraph_sdk import get_client, get_sync_client
from starlette.config import Config
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

app = FastAPI()

load_dotenv()
# Replace these with your own OAuth settings
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
SECRET_KEY = os.getenv("SECRET_KEY")
config_data = {'GOOGLE_CLIENT_ID': GOOGLE_CLIENT_ID, 'GOOGLE_CLIENT_SECRET': GOOGLE_CLIENT_SECRET}
starlette_config = Config(environ=config_data)
oauth = OAuth(starlette_config)
oauth.register(
    name='google',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
API_URL = os.getenv("LANGGRAPH_API_URL", "http://localhost:2024")
ASSISTANT_ID = "agent"  # must match your langgraph.json
API_KEY = os.getenv("LANGSMITH_API_KEY")
client = get_client(url=API_URL, api_key=API_KEY)  # async client


async def ensure_thread(thread_id):
    if thread_id:
        return thread_id
    t = await client.threads.create()
    return t["thread_id"]


# Dependency to get the current user
def get_user(request: Request):
    user = request.session.get('user')
    epoch_time = int(time.time())

    if (user is not None and user['exp'] is not None) and int(user['exp']) >= epoch_time:
        return user['name']

    return None


@app.get('/')
def public(user: dict = Depends(get_user)):
    if user:
        return RedirectResponse(url='/gradio')
    else:
        return RedirectResponse(url='/login-demo')


@app.route('/logout')
async def logout(request: Request):
    request.session.pop('user', None)
    return RedirectResponse(url='/')


@app.route('/login')
async def login(request: Request):
    redirect_uri = request.url_for('auth')

    if urlparse(str(redirect_uri)).scheme == "https":
        redirect_uri = urlunparse(urlparse(str(redirect_uri))._replace(scheme='https'))

    # If your app is running on https, you should ensure that the
    # `redirect_uri` is https, e.g. uncomment the following lines:
    #
    # from urllib.parse import urlparse, urlunparse
    #redirect_uri = urlunparse(urlparse(str(redirect_uri))._replace(scheme='https'))

    resp = oauth.google.authorize_redirect(request, redirect_uri, prompt='select_account', max_age=0)
    return await resp


@app.route('/auth')
async def auth(request: Request):
    try:
        access_token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        return RedirectResponse(url='/')
    request.session['user'] = dict(access_token)["userinfo"]
    return RedirectResponse(url='/')


with gr.Blocks() as login_demo:
    gr.Button("Login", link="/login")

app = gr.mount_gradio_app(app, login_demo, path="/login-demo")

with (gr.Blocks(theme=gr.themes.Soft(), css=""".svelte-vzs2gq {display: none;}
    .note-text {
    font-size: 0.85em;
    color: #666666;   /* grey */
    margin-top: 1px;
    margin-bottom: 3px;
    font-style: italic;
}
""") as main_demo):
    redirector = gr.Textbox(visible=False)
    redirector_url = gr.State(None)
    title = gr.Markdown("## 💬 Mohamed's Agent")
    persona = gr.Radio(["Talk to Mohamed's agent", "Talk to Mohamed"],
                       value="Talk to Mohamed's agent", label="Persona")
    disclosure = gr.Markdown("Note: Information provided by Mohamed’s AI career assistant, "
                             "based on his professional materials.", elem_classes="note-text")
    persona_value = gr.State("agent")

    gr.Markdown("### Instructions")
    gr.Markdown("✅ Please ask questions related to only professional experience and technology")
    gr.Markdown("✅ Little casual talk is fine but don't go too far 🙂")

    chatbot = gr.Chatbot(
        label="Let’s Chat! 💬",
        height=400,
        bubble_full_width=False,
        show_copy_button=False,
        show_copy_all_button=False,
        show_share_button=False,
        resizable=True
    )

    chatbot.value = [
        (None, "👋 " + "Hi there!"),
    ]

    msg = gr.Textbox(
        placeholder="Type your message here...",
        show_label=False,
        container=False,
        elem_id="msg_box"
    )


    def on_persona_change(p):
        show = (p == "Talk to Mohamed")
        if show:
            chat_title = gr.Markdown("## 💬 Mohamed as ChatBot")
            disclaimer = gr.update(visible=show,
                                   value="⚠️ Note: This chatbot generates responses in Mohamed’s voice "
                                         "based on his professional materials. "
                                         "It is AI-assisted and not always 100% accurate."
                                   )
            persona = gr.update(interactive=False)
            persona_value = "me"
        else:
            chat_title = gr.Markdown("## 💬 Mohamed's Agent")
            disclaimer = gr.update(visible=True,
                                   value="Note: Information provided by Mohamed’s AI career assistant, "
                                         "based on his professional materials.")
            persona = gr.update(interactive=False)
            persona_value = "agent"
        return chat_title, disclaimer, persona, persona_value


    def extract_text(piece):
        """Be tolerant to different stream chunk shapes; return text delta if present."""
        if piece and 'messages' in piece:
            resp_msg = piece["messages"][-1]
            if (resp_msg['type'] == "ai"):
                return resp_msg["content"]
        return ""


    persona.change(on_persona_change, persona, [title, disclosure, persona, persona_value])


    def update_value(r_url):
        return r_url


    SPINNER = ["🌍", "🌎", "🌏"]


    async def respond(message, history, thread_id, persona_option):
        # 1) Persist or create a thread (so responses have conversation memory)
        thread_id = await ensure_thread(thread_id)

        # 2) Show the user bubble immediately
        history = history + [(message, "")]
        yield "", history, thread_id, gr.update(), ""

        # 3) Stream the assistant’s reply from LangGraph
        assistant_text = None
        done = False
        if persona_option == "Talk to Mohamed's agent":
            reply = f"As Mohamed’s agent:\n"
            persona_value_str = "agent"
        else:
            reply = f"I: "
            persona_value_str = "me"

        async def reader():
            nonlocal assistant_text, done
            async for chunk in client.runs.stream(
                    thread_id,
                    ASSISTANT_ID,
                    input={"messages": [{"role": "user", "content": message}]},
                    stream_mode="values",  # adjust if you prefer "updates"
                    context={"ctr_th": 10, "courtesy_ctr_th": 3, "personal_ctr_th": 3, "persona": persona_value_str}
            ):
                text_delta = extract_text(getattr(chunk, "data", ""))  # pull out any text
                if text_delta:
                    assistant_text = (assistant_text or "") + text_delta
                    #history[-1] = (message, f"{reply}{assistant_text}")  # update the last assistant bubble
                    #yield "", history, thread_id, gr.update(), ""
                done = True

        task = asyncio.create_task(reader())

        i = 0

        while not done and assistant_text is None:
            history[-1] = (message, f"{SPINNER[i % len(SPINNER)]} _typing…_")
            i += 1
            yield "", history, thread_id, gr.update(), ""
            await asyncio.sleep(0.05)

        while not done:
            print(f"assistant_text in not done is {assistant_text}")
            history[-1] = (message, f"{reply}{assistant_text or ''}")
            yield "", history, thread_id, gr.update(), ""
            await asyncio.sleep(0.05)

        await task

        if assistant_text.find("Good Bye") != -1:
            history[-1] = (message, f"{reply}{assistant_text or ''}")
            yield ("", history, None, gr.update(interactive=False,
                                                placeholder="Logging Out - exceeding casual talk threshold"),
                   "/logout")
        else:
            history[-1] = (message, f"{reply}{assistant_text or ''}")
            yield "", history, thread_id, gr.update(), ""


    thread_state = gr.State(None)  # holds LangGraph thread_id
    msg.submit(respond, [msg, chatbot, thread_state, persona], [msg, chatbot, thread_state, msg, redirector])
    redirector.change(
        lambda x: x,
        redirector,
        None,
        js="""(redirector) => {
                console.log(redirector)
                if (redirector === "/logout") {
                    //window.location.href = redirector;
                    setTimeout(() => { window.location.href = redirector; }, 3000);
                }
            }"""
    )

app = gr.mount_gradio_app(app, main_demo, path="/gradio", auth_dependency=get_user)

if __name__ == '__main__':
    uvicorn.run(app)
