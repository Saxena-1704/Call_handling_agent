import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

account_sid = os.environ["TWILIO_ACCOUNT_SID"]
auth_token = os.environ["TWILIO_AUTH_TOKEN"]
twilio_number = os.environ["TWILIO_PHONE_NUMBER"]
my_number = os.environ["MY_PHONE_NUMBER"]
ws_url = os.environ["TWILIO_WS_URL"]

ngrok_base = ws_url.replace("wss://", "https://").replace("/twilio/media-stream", "")

client = Client(account_sid, auth_token)

call = client.calls.create(
    url=f"{ngrok_base}/twilio/incoming_call",
    method="POST",
    to=my_number,
    from_=twilio_number,
)

print(f"Call initiated: {call.sid}")
