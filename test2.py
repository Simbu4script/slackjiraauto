import os
import json
import logging
import requests
import threading
from flask import Flask, request
from slack_bolt import App as SlackApp
from slack_sdk.errors import SlackApiError
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
                        "text": {"type": "plain_text", "text": "FAQ"},
                        "url": JIRA_PORTAL_URL,
                        "action_id": "FAQ"
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
# --- Action: FAQ (URL button) ---
@slack_app.action("FAQ")
def handle_FAQ(ack):
    ack()    
# --- Action: Login Issue (Triggers Modal) ---
@slack_app.action("login_issue_modal")  # or wherever you trigger modal
def open_login_issue_modal(ack, body, client):
    ack()

    trigger_id = body["trigger_id"]
    channel_id = body["channel"]["id"]
    thread_ts = body.get("thread_ts") or body.get("message", {}).get("ts")

    client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": "submit_login_issue",
            "private_metadata": json.dumps({
                "channel_id": channel_id,
                "thread_ts": thread_ts
            }),
            "title": {"type": "plain_text", "text": "Login Issue"},
            "submit": {"type": "plain_text", "text": "Submit"},
            "blocks": [
                {
                    "type": "input",
                    "optional": True,
                    "block_id": "participant_email",
                    "label": {"type": "plain_text", "text": "Participant Email(e.g:issuer)"},
                    "element": {"type": "plain_text_input", "action_id": "input"}
                },
                {
                    "type": "input",
                    "block_id": "summary",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input"
                    },
                    "label": {"type": "plain_text", "text": "Summary"}
                },
                {
                    "type": "input",
                    "block_id": "description",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "input",
                        "multiline": True
                    },
                    "label": {"type": "plain_text", "text": "Description"}
                }
            ]
        }
    )


# --- Handle Form Submission ---
@slack_app.view("submit_login_issue")
def handle_login_issue_submission(ack, body, client, logger):
    ack()  # Acknowledge immediately

    # Defer heavy Jira ticket creation to a background thread
    threading.Thread(target=process_login_issue_submission, args=(body, client, logger)).start()


def process_login_issue_submission(body, client, logger):
    try:
        values = body["view"]["state"]["values"]
        
        participant_email = values.get("participant_email", {}).get("input", {}).get("value", "").strip()
        summary = values["summary"]["input"]["value"]
        description_text = values["description"]["input"]["value"]

        # Read metadata from modal
        metadata = json.loads(body["view"].get("private_metadata", "{}"))
        channel_id = metadata.get("channel_id")
        thread_ts = metadata.get("thread_ts")

        participant_account_id = None
        if participant_email:
            user_search = requests.get(
                f"{JIRA_BASE_URL}/rest/api/3/user/search?query={participant_email}",
                auth=(JIRA_EMAIL, JIRA_API_TOKEN),
                headers={"Accept": "application/json"}
            )
            if user_search.status_code == 200:
                users = user_search.json()
                if users:
                    participant_account_id = users[0].get("accountId")
                else:
                    logger.error(f"No Jira user found for participant email {participant_email}")
            else:
                logger.error(f"Failed to search Jira user for email {participant_email}: {user_search.text}")

        # Prepare Jira request
        payload = {
            "serviceDeskId": 1,
            "requestTypeId": 4,
            "requestFieldValues": {
                "summary": summary,
                "description": description_text,
            }
        }
        if participant_account_id:
            payload["requestParticipants"] = [participant_account_id]

        response = requests.post(
            f"{JIRA_BASE_URL}/rest/servicedeskapi/request",
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=payload
        )

        if response.status_code == 201:
            issue = response.json()
            issue_key = issue.get("issueKey")
            issue_url = f"{JIRA_BASE_URL}/browse/{issue_key}"

            message = (
                f"🆕 *Login Issue Submitted!*\n"
                f"• *Participant*: `{participant_email or 'N/A'}`\n"
                f"• *Summary*: {summary}\n"
                f"• *Jira Ticket*: <{issue_url}|{issue_key}>"
            )

            # Post to thread if available
            if channel_id and thread_ts:
                client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=message)

            # Notify admin channel
            client.chat_postMessage(channel=ADMIN_CHANNEL_ID, text=message)

        else:
            error_message = f"❌ Jira ticket creation failed.\n{response.text}"
            logger.error(error_message)

            if channel_id and thread_ts:
                client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=error_message)

            client.chat_postMessage(channel=ADMIN_CHANNEL_ID, text=error_message)

    except Exception as e:
        logger.exception("Jira submission failed")

        error_message = f"❌ Error creating Jira ticket. Error: {str(e)}"
        if channel_id and thread_ts:
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=error_message)

        client.chat_postMessage(channel=ADMIN_CHANNEL_ID, text=error_message)

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

        # Step 1: Get Slack user's email
        user_info = client.users_info(user=user_id)
        email = user_info["user"]["profile"]["email"]

        # Step 2: Get Jira accountId for that email
        jira_user_response = requests.get(
            f"{JIRA_BASE_URL}/rest/api/3/user/search?query={email}",
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json"}
        )
        if jira_user_response.status_code != 200 or not jira_user_response.json():
            client.chat_postEphemeral(channel=channel_id, user=user_id,
                text="❌ Could not find a matching Jira account for your Slack email.")
            return

        jira_user_account_id = jira_user_response.json()[0]["accountId"]

        # Step 3: Fetch Jira ticket
        response = requests.get(
            f"{JIRA_BASE_URL}/rest/api/3/issue/{ticket_key}",
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json"}
        )

        if response.status_code != 200:
            client.chat_postEphemeral(channel=channel_id, user=user_id,
                text=f"❌ Ticket *{ticket_key}* not found or inaccessible.")
            return

        ticket = response.json()
        reporter = ticket["fields"]["reporter"]["accountId"]
        assignee = ticket["fields"]["assignee"]["accountId"] if ticket["fields"]["assignee"] else None
        comments = ticket["fields"]["comment"]["comments"]
        commenters = {c["author"]["accountId"] for c in comments}
        allowed_users = {reporter} | commenters
        if assignee:
            allowed_users.add(assignee)

        # Step 4: Check permission
        if jira_user_account_id not in allowed_users:
            client.chat_postEphemeral(channel=channel_id, user=user_id,
                text="⛔ You are not authorized to view this ticket status.")
            return

        # Step 5: Show status
        summary = ticket["fields"]["summary"]
        status = ticket["fields"]["status"]["name"]
        url = f"{JIRA_BASE_URL}/browse/{ticket_key}"

        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            thread_ts=thread_ts,
            text=f"🔍 *Ticket:* <{url}|{ticket_key}>\n📌 *Summary:* {summary}\n📄 *Status:* *{status}*"
        )

        # Optional: log to admin channel
        client.chat_postMessage(
            channel=ADMIN_CHANNEL_ID,
            text=f"📣 <@{user_id}> checked status of <{url}|{ticket_key}>: *{status}*"
        )

    except SlackApiError as e:
        logger.error(f"Slack error: {e.response['error']}")
        client.chat_postEphemeral(channel=channel_id, user=user_id,
            text="⚠️ Slack error occurred.")
    except Exception as e:
        logger.exception("Unexpected error")
        client.chat_postEphemeral(channel=channel_id, user=user_id,
            text="⚠️ An error occurred while checking the ticket status.")

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
