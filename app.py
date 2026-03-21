import os
import json
import base64
import requests
import threading
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, ImageMessageContent

app = Flask(__name__)

CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')
CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
CHILDREN_INFO = os.environ.get('CHILDREN_INFO', '')

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

def get_image(message_id):
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    headers = {"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"}
    res = requests.get(url, headers=headers)
    return base64.b64encode(res.content).decode('utf-8')

def analyze(img_b64):
    prompt = f"""この画像は日本の学校・保育園の行事プリントです。
子供: {CHILDREN_INFO}
行事を読み取りJSONのみ返してください:
{{"events":[{{"title":"行事名","date":"YYYY-MM-DD","endDate":"YYYY-MM-DD","details":"持ち物など"}}]}}
年不明なら2026年で推定。"""
    res = requests.post('https://openrouter.ai/api/v1/chat/completions',
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {OPENROUTER_API_KEY}'},
        json={'model': 'google/gemini-2.0-flash-001',
              'messages': [{'role': 'user', 'content': [
                  {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{img_b64}'}},
                  {'type': 'text', 'text': prompt}]}]},
        timeout=30)
    text = res.json()['choices'][0]['message']['content']
    return json.loads(text.replace('```json', '').replace('```', '').strip())

def gcal(ev):
    from urllib.parse import quote
    f = lambda d: d.replace('-', '')
    return f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={quote(ev['title'])}&dates={f(ev['date'])}/{f(ev['endDate'])}&details={quote(ev.get('details',''))}"

def process_image(message_id, reply_token):
    try:
        img_b64 = get_image(message_id)
        result = analyze(img_b64)
        msgs = []
        events = result if isinstance(result, list) else result.get('events', [])
        for ev in events:
            msgs.append(f"{ev['title']}\n{ev['date']}\n{gcal(ev)}")
        reply = '\n\n'.join(msgs) if msgs else '行事が見つかりませんでした'
    except Exception as e:
       reply = f'エラー: {str(e)}'
    
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=reply[:5000])]))

@app.route('/webhook', methods=['POST'])
def webhook():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    thread = threading.Thread(
        target=process_image,
        args=(event.message.id, event.reply_token))
    thread.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
