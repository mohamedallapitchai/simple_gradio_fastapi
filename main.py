import hashlib
import os
import time

import gradio as gr
import uvicorn
from authlib.integrations.starlette_client import OAuth, OAuthError
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Request
from starlette.config import Config
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
import os

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


# Dependency to get the current user
def get_user(request: Request):
    user = request.session.get('user')
    print(f"user is {user}")

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
    # print(f"redirect_uri: {redirect_uri}")
    # If your app is running on https, you should ensure that the
    # `redirect_uri` is https, e.g. uncomment the following lines:
    #
    # from urllib.parse import urlparse, urlunparse
    # redirect_uri = urlunparse(urlparse(str(redirect_uri))._replace(scheme='https'))
    # print(f"request in /login is {request}")

    print(f"request.args.get state is {request.session.get('state'), 'NOT_AVAILABLE'}")
    resp = oauth.google.authorize_redirect(request, redirect_uri)
    print(resp)
    return await resp


@app.route('/auth')
async def auth(request: Request):
    try:
        # print(f"request in /auth is {request}")
        access_token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        return RedirectResponse(url='/')
    request.session['user'] = dict(access_token)["userinfo"]
    return RedirectResponse(url='/')


with gr.Blocks() as login_demo:
    gr.Button("Login", link="/login")

app = gr.mount_gradio_app(app, login_demo, path="/login-demo")

with gr.Blocks(theme=gr.themes.Soft(), css=""".svelte-vzs2gq {display: none;}
    .note-text {
    font-size: 0.85em;
    color: #666666;   /* grey */
    margin-top: 1px;
    margin-bottom: 3px;
    font-style: italic;
}
""") as main_demo:
    title = gr.Markdown("## 💬 Mohamed's Agent")
    persona = gr.Radio(["Agent (3rd person)", "Speak as Me (1st person)"],
                       value="Agent (3rd person)", label="Persona")
    disclosure = gr.Markdown("Note: Information provided by Mohamed’s AI career assistant, "
                             "based on his professional materials.", elem_classes="note-text")

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
        container=False
    )


    def on_persona_change(p):
        show = (p == "Speak as Me (1st person)")
        if show:
            chat_title = gr.Markdown("## 💬 Mohamed as ChatBot")
            disclaimer = gr.update(visible=show,
                                   value="⚠️ Note: This chatbot generates responses in Mohamed’s voice "
                                         "based on his professional materials. "
                                         "It is AI-assisted and not always 100% accurate."
                                   )
        else:
            chat_title = gr.Markdown("## 💬 Mohamed's Agent")
            disclaimer = gr.update(visible=True,
                                   value="Note: Information provided by Mohamed’s AI career assistant, "
                                         "based on his professional materials.")
        return chat_title, disclaimer


    persona.change(on_persona_change, persona, [title, disclosure])


    def respond(message, history, persona_option):
        # Plug your RAG/LLM call here using `system` and retrieved chunks.
        # For demo purposes:
        if persona_option == "Agent (3rd person)":
            reply = f"As Mohamed’s agent:\n {message}"
        else:
            note = " (AI-generated from Mohamed’s materials)"
            print(f"len history is {len(history)}")
            first = len(history) == 0
            reply = f"I: {message}{note if first else ''}"
        history.append((message, reply))
        return "", history


    # def respond(message, chat_history):
    #     bot_message = f"🤖 You said: {message}"
    #     chat_history.append((message, bot_message))
    #     return "", chat_history

    msg.submit(respond, [msg, chatbot, persona], [msg, chatbot])

    # msg.submit(respond, [msg, chatbot], [msg, chatbot])

app = gr.mount_gradio_app(app, main_demo, path="/gradio", auth_dependency=get_user)

if __name__ == '__main__':
    uvicorn.run(app)
