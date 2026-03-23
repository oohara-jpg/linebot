import os
import json
import base64
import requests
import threading
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, TemplateMessage, ButtonsTemplate, PostbackAction
from linebot.v3.webhooks import MessageEvent, ImageMessageContent, PostbackEvent

app = Flask(__name__)

CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')
CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

CHILDREN = {
    'ryuso': {'name': '隆蒼', 'school': '堀江小学校'},
    'yuso': {'name': '夕蒼', 'school': '敬愛保育園'},
    'kazuka_momoka': {'name': '一華・百華', 'school': '敬愛保育園'},
    'soka': {'name': '颯華', 'school': '敬愛保育園'},
}

pending_images = {}

def get_image(message_id):
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    headers = {"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"}
    res = requests.get(url, headers=headers)
    return base64.b64encode(res.content).decode('utf-8')

def analyze(img_b64, child_info):
    prompt = f"""この画像は日本の学校・保育園の行事プリントです。
子供: {child_info['name']}（{child_info['school']}）
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
    if not text:
        return {'events': []}
    return json.loads(text.replace('```json', '').replace('```', '').strip())

def gcal(ev):
    from urllib.parse import quote
    f = lambda d: d.replace('-', '')
    date = ev.get('date', '20260101')
    end_date = ev.get('endDate', date)
    return f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={quote(ev['title'])}&dates={f(date)}/{f(end_date)}&details={quote(ev.get('details',''))}"

def process_image(img_b64, reply_token, child_info):
    try:
        result = analyze(img_b64, child_info)
        msgs = []
        events = result if isinstance(result, list) else result.get('events', [])
        for ev in events:
            msgs.append(f"{ev['title']}\n{ev.get('date','')}\n{gcal(ev)}")
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
    user_id = event.source.user_id
    message_id = event.message.id
    reply_token = event.reply_token
    
    img_b64 = get_image(message_id)
    pending_images[user_id] = img_b64
    
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TemplateMessage(
                alt_text='お子さんを選んでください',
                template=ButtonsTemplate(
                    text='どのお子さんのプリントですか？',
                    actions=[
                        PostbackAction(label='隆蒼', data='child=ryuso'),
                        PostbackAction(label='夕蒼', data='child=yuso'),
                        PostbackAction(label='一華・百華', data='child=kazuka_momoka'),
                        PostbackAction(label='颯華', data='child=soka'),
                    ]
                )
            )]))

@handler.add(PostbackEvent)
def handle_postback(event):
    user_id = event.source.user_id
    data = event.postback.data
    reply_token = event.reply_token
    
    if data.startswith('child='):
        child_key = data.split('=')[1]
        child_info = CHILDREN.get(child_key)
        img_b64 = pending_images.pop(user_id, None)
        
        if not img_b64:
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).reply_message(ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text='画像が見つかりません。もう一度画像を送ってください。')]))
            return
        
        thread = threading.Thread(
            target=process_image,
            args=(img_b64, reply_token, child_info))
        thread.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
