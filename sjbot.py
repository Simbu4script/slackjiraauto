import os
import json
import logging
import requests
from flask import Flask, request
from slack_bolt import App as SlackApp
from slack_bolt.adapter.flask import SlackRequestHandler
from dotenv import load_dotenv

# Logging
logging.basicConfig(level=logging.DEBUG)

# Load environment variables
load_dotenv()

# Flask app
flask_app = Flask(__name__)

# Slack Bolt app
slack_app = SlackApp(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"]
)

handler = SlackRequestHandler(slack_app)

TARGET_USER = os.environ.get("TARGET_USER_ID", "<@UXXXXXXX>")
JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY")
ADMIN_CHANNEL_ID = os.environ.get("ADMIN_CHANNEL_ID")
JIRA_PORTAL_URL = os.environ.get("JIRA_PORTAL_URL")

# Handle mentions (when a specific user is mentioned)
@slack_app.event("message")
def handle_user_mention(event, say, client):
    text = event.get("text", "")
    subtype = event.get("subtype", "")

    if TARGET_USER in text and subtype == "":
        user = event["user"]
        channel = event["channel"]
        ts = event["ts"]

        say(channel=channel, thread_ts=ts, text=f"Hi <@{user}> 👋! How can I help you?")
        client.reactions_add(channel=channel, name="eyes", timestamp=ts)

        say(
            channel=channel,
            thread_ts=ts,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": "Choose an option below to proceed."}},
                {"type": "actions", "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Create Jira Ticket"},
                        "url": JIRA_PORTAL_URL,
                        "action_id": "open_jsm_portal"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Login Issue"},
                        "action_id": "login_issue_modal"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Check Ticket Status"},
                        "action_id": "check_ticket_status"
                    }
                ]}
            ],
            text="Choose an option to proceed"
        )

# --- Action: Create Jira Ticket (URL button) ---
@slack_app.action("open_jsm_portal")
def handle_open_jsm_portal(ack):
    ack()

# --- Action: Login Issue (Triggers Modal) ---
@slack_app.action("login_issue_modal")
def handle_login_issue_modal_action(ack, body, client):
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "submit_login_issue",
            "title": {"type": "plain_text", "text": "Login Issue"},
            "submit": {"type": "plain_text", "text": "Submit"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "user_id",
                    "label": {"type": "plain_text", "text": "User (on behalf of)"},
                    "element": {
                        "type": "users_select",
                        "action_id": "select_user"
                    }
                },
                {
                    "type": "input",
                    "block_id": "summary",
                    "label": {"type": "plain_text", "text": "Summary"},
                    "element": {"type": "plain_text_input", "action_id": "input"}
                },
                {
                    "type": "input",
                    "block_id": "description",
                    "label": {"type": "plain_text", "text": "Description"},
                    "element": {"type": "plain_text_input", "action_id": "input", "multiline": True}
                }
            ]
        }
    )

# --- Handle select user (optional logging) ---
@slack_app.action("select_user")
def handle_select_user(ack, body, logger):
    ack()
    logger.info(body)

# --- Handle Login Issue Form Submission ---
@slack_app.view("submit_login_issue")
def handle_login_issue_submission(ack, body, client, logger):
    ack()
    try:
        user_id = body["view"]["state"]["values"]["user_id"]["select_user"]["selected_user"]
        summary = body["view"]["state"]["values"]["summary"]["input"]["value"]
        description_text = body["view"]["state"]["values"]["description"]["input"]["value"]

        response = requests.post(
            f"{JIRA_BASE_URL}/rest/api/3/issue",
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={
                "fields": {
                    "project": {"key": JIRA_PROJECT_KEY},
                    "summary": summary,
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": description_text}]}
                        ]
                    }, 
                    "issuetype": {"name": "Task"}
                }
            }
        )

        ticket = response.json()
        if response.status_code == 201:
            issue_key = ticket.get("key")
            ticket_url = f"{JIRA_BASE_URL}/browse/{issue_key}"

            client.chat_postMessage(
                channel=ADMIN_CHANNEL_ID,
                text=f"🔔 *New Login Issue* reported by <@{user_id}>:\n*Summary*: {summary}\n*Description*: {description_text}\n*Jira Ticket*: <{ticket_url}|{issue_key}>"
            )
        else:
            logger.error(f"Jira error: {response.text}")
            client.chat_postMessage(
                channel=ADMIN_CHANNEL_ID,
                text=f"❌ Failed to create Jira ticket for <@{user_id}>. Please check the logs."
            )

    except Exception as e:
        logger.exception("Exception while creating Jira ticket")
        client.chat_postMessage(
            channel=ADMIN_CHANNEL_ID,
            text=f"❌ An error occurred while creating a Jira ticket for <@{user_id}>. Please check the logs."
        )

# --- Action: Check Ticket Status ---
@slack_app.action("check_ticket_status")
def handle_check_ticket_status_action(ack, body, client):
    ack()
    trigger_id = body["trigger_id"]
    client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": "submit_ticket_status",
            "title": {"type": "plain_text", "text": "Check Ticket Status"},
            "submit": {"type": "plain_text", "text": "Check"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "private_metadata": json.dumps({
                "channel": body["channel"]["id"],
                "ts": body["message"]["ts"]
            }),
            "blocks": [
                {
                    "type": "input",
                    "block_id": "ticket_id_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "ticket_id_input",
                        "placeholder": {"type": "plain_text", "text": "Enter ticket number (e.g. 12345)"}
                    },
                    "label": {"type": "plain_text", "text": "Ticket Number"}
                }
            ]
        }
    )

# --- View Submit: Handle Ticket Status Form ---
@slack_app.view("submit_ticket_status")
def handle_submit_ticket_status(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        state_values = body["view"]["state"]["values"]
        ticket_number = state_values["ticket_id_block"]["ticket_id_input"]["value"].strip()
        ticket_key = f"{JIRA_PROJECT_KEY}-{ticket_number.upper()}"

        metadata = json.loads(body["view"]["private_metadata"])
        thread_ts = metadata["ts"]
        channel_id = metadata["channel"]

        response = requests.get(
            f"{JIRA_BASE_URL}/rest/api/3/issue/{ticket_key}",
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json"}
        )

        if response.status_code == 200:
            ticket = response.json()
            status = ticket["fields"]["status"]["name"]
            summary = ticket["fields"]["summary"]
            url = f"{JIRA_BASE_URL}/browse/{ticket_key}"

            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"🔍 *Ticket:* <{url}|{ticket_key}>\n📌 *Summary:* {summary}\n📄 *Status:* *{status}*"
            )

            client.chat_postMessage(
                channel=ADMIN_CHANNEL_ID,
                text=f"📣 <@{user_id}> checked status of <{url}|{ticket_key}>: *{status}*"
            )
        else:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"❌ Ticket *{ticket_key}* not found or you don’t have access."
            )

    except Exception as e:
        logger.exception("Error checking ticket status")
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="⚠️ Error while checking the ticket status."
        )

# Slack Events Route
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

# Health Check
@flask_app.route("/")
def health():
    return "✅ Bot is running"

if __name__ == "__main__":
    flask_app.run(port=3000)
