"""Interactive Cedar Crumb host with actual MCP activity and saved inquiries."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
import gradio as gr
from openai import AuthenticationError, RateLimitError, NotFoundError

from bakery import ROOT, inspect_inquiries, next_pickup_at
from client import build_agent, walkthrough_steps
from session_runtime import SessionRuntime

RUNTIMES = {}

CATEGORIES = {"search_menu": "Data", "save_pickup_inquiry": "Action", "prepare_pickup_inquiry": "Orchestration"}


def new_session():
    return {"history": [], "database": str(ROOT / "results" / f"chat-{uuid4().hex}.sqlite3"), "events": []}


def records(database):
    if not Path(database).exists():
        return []
    return [[r["inquiry_id"], r["product_name"], r["quantity"], r["estimated_total_usd"], r["pickup_at"], r["status"]]
            for r in inspect_inquiries(database)]


def activity_rows(events):
    if not events:
        return []
    current = [e for e in events if e["request"] == events[-1]["request"]]
    rows = []
    for index, event in enumerate(current, 1):
        detail = event.get("cache") or event.get("tool") or ""
        if "definitions" in event:
            detail = ", ".join(t["name"] for t in event["definitions"])
        if "result" in event:
            detail = "Error returned" if event.get("is_error") else "Result returned"
        rows.append([str(index), event["direction"], event.get("operation", "message"), detail])
    return rows


def runtime_for(session):
    key = session["database"]
    runtime = RUNTIMES.get(key)
    loop = asyncio.get_running_loop()
    if runtime is None or runtime.closing or runtime.owner.done() or runtime.loop is not loop:
        if runtime is not None:
            runtime.request_close()
        runtime = SessionRuntime(key)
        RUNTIMES[key] = runtime
    return runtime


def release_session(session):
    if session:
        runtime = RUNTIMES.pop(session["database"], None)
        if runtime is not None:
            runtime.request_close()


async def reset_session(session):
    runtime = RUNTIMES.pop(session["database"], None) if session else None
    if runtime is not None:
        await runtime.close()
    return "", [], new_session(), [], []


async def clear_session(session):
    _, _, fresh, events, rows = await reset_session(session)
    return fresh, events, rows


async def respond(message, chat, session):
    session = session or new_session()
    chat = list(chat or [])
    if not message.strip():
        yield "", chat, session, session["events"], records(session["database"])
        return
    request_ref = "turn-" + uuid4().hex[:8]
    def record(direction, **details):
        if "tool" in details:
            details["category"] = CATEGORIES.get(details["tool"])
        session["events"].append({"request": request_ref, "direction": direction, **details})
    record("Customer → Host", operation="customer request", message=message)
    chat += [{"role": "user", "content": message}, {"role": "assistant", "content": "Checking your request…"}]
    yield "", chat, session, session["events"], records(session["database"])
    try:
        runtime = runtime_for(session)
        async for kind, payload in runtime.run(message, session["history"], build_agent):
            if kind == "event":
                record(**payload)
                yield "", chat, session, session["events"], records(session["database"])
            elif kind == "answer":
                answer, session["history"] = payload
                chat[-1] = {"role": "assistant", "content": answer}
                record("Host → Customer", operation="final answer", answer=answer)
            elif kind == "error":
                raise payload
    except Exception as error:
        if isinstance(error, AuthenticationError):
            explanation = "Gemini rejected the API key. Check GEMINI_API_KEY and restart the app."
        elif isinstance(error, RateLimitError):
            explanation = "Gemini's rate or quota limit was reached. Check your API quota and retry later."
        elif isinstance(error, NotFoundError):
            explanation = "The configured Gemini model is unavailable. Check GEMINI_MODEL and restart the app."
        else:
            explanation = "The request could not finish. Check the runtime logs and connection. Inspect saved inquiries before retrying; reuse the same request reference."
        chat[-1] = {"role": "assistant", "content": explanation}
        record("Host → Customer", operation="error", error=explanation)
    yield "", chat, session, session["events"], records(session["database"])


def create_app():
    menu = json.loads((ROOT / "data/bakery.json").read_text(encoding="utf-8"))
    with gr.Blocks(title="Cedar Crumb") as app:
        gr.Markdown("# Cedar Crumb\n**Good bread. Time well spent.**\n\nExplore our breads and prepare your pickup inquiry with Nour.")
        session = gr.State(value=new_session, time_to_live=900, delete_callback=release_session)
        with gr.Row():
            with gr.Column(scale=3):
                chat = gr.Chatbot(label="Nour · Bread guide & pickup assistant", height=450)
                message = gr.Textbox(label="Your message", placeholder="Ask about our breads, or arrange a pickup inquiry…", lines=2)
                with gr.Row():
                    send = gr.Button("Send", variant="primary")
                    reset = gr.Button("New conversation")
                gr.Examples(examples=[[p[0]] for p in walkthrough_steps(next_pickup_at())], inputs=message, label="Try a request · click, then Send")
            with gr.Column(scale=2):
                with gr.Tab("Menu"):
                    gr.Dataframe(value=[[p["name"], p["weight_g"], p["price_usd"]] for p in menu["products"]],
                        headers=["Bread", "Weight (g)", "USD"], interactive=False)
                    gr.Markdown("**Hamra, Beirut** · Monday–Saturday, 07:00–19:00.\n\nPickup inquiries need 24 hours' notice and await staff confirmation. All breads contain gluten; ask Nour for ingredients and allergen details.")
                with gr.Tab("Tool activity"):
                    timeline = gr.Dataframe(value=[], headers=["Step", "Direction", "Operation", "Detail"],
                        label="Current request", interactive=False)
                    activity = gr.JSON(value=[], label="Actual MCP calls and returned results")
                with gr.Tab("Saved inquiries"):
                    saved = gr.Dataframe(headers=["Reference", "Bread", "Quantity", "USD estimate", "Pickup", "Status"], value=[], interactive=False)
                    refresh = gr.Button("Refresh inquiries")
        gr.Markdown("Contact details are saved only with your agreement for pickup follow-up. Menu information and conversation content are processed by Gemini.")
        inputs = [message, chat, session]
        outputs = [message, chat, session, activity, saved]
        send.click(respond, inputs, outputs, concurrency_limit=1, concurrency_id="chat")
        message.submit(respond, inputs, outputs, concurrency_limit=1, concurrency_id="chat")
        reset.click(reset_session, inputs=session, outputs=outputs,
            concurrency_limit=1, concurrency_id="chat")
        chat.clear(clear_session, inputs=session, outputs=[session, activity, saved],
            concurrency_limit=1, concurrency_id="chat")
        refresh.click(lambda s: records(s["database"]), session, saved)
        activity.change(activity_rows, inputs=activity, outputs=timeline, queue=False)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--share", action="store_true")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    create_app().queue().launch(server_name="127.0.0.1", server_port=args.port, share=args.share,
        theme=gr.themes.Soft(primary_hue="amber", neutral_hue="stone"), show_error=False)
