# v_final
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
import json
import threading
import uuid

app = Flask(__name__)
CORS(app)

jobs = {}

@app.route('/health', methods=['GET'])
def health():
    try:
        import youtube_transcript_api.proxies as p
        proxy_classes = [x for x in dir(p) if not x.startswith('_')]
    except Exception as e:
        proxy_classes = [str(e)]
    return jsonify({'status': 'ok', 'proxy_classes': proxy_classes})

@app.route('/transcribe/start', methods=['POST'])
def transcribe_start():
    try:
        data = request.json
        video_url = data.get('url', '')
        if not video_url:
            return jsonify({'error': 'No video URL provided'}), 400
        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {'status': 'processing'}
        thread = threading.Thread(target=do_transcription, args=(job_id, video_url))
        thread.daemon = True
        thread.start()
        return jsonify({'job_id': job_id, 'status': 'processing'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/transcribe/status/<job_id>', methods=['GET'])
def transcribe_status(job_id):
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    job = jobs[job_id]
    if job['status'] == 'done':
        transcript = job['transcript']
        del jobs[job_id]
        return jsonify({'status': 'done', 'transcript': transcript})
    elif job['status'] == 'error':
        error = job['error']
        del jobs[job_id]
        return jsonify({'status': 'error', 'error': error}), 400
    return jsonify({'status': 'processing'})

def do_transcription(job_id, video_url):
    try:
        vid_match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', video_url)
        if not vid_match:
            jobs[job_id] = {'status': 'error', 'error': 'Could not extract video ID'}
            return

        video_id = vid_match.group(1)
        print(f'Getting transcript for: {video_id}')

        from youtube_transcript_api import YouTubeTranscriptApi
        import youtube_transcript_api.proxies as proxy_module

        proxy_user = 'yjaztwpe'
        proxy_pass = '5kbkztiemifw'
        proxy_host = '31.59.20.176'
        proxy_port = '6754'
        proxy_url = f'http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}'

        # Try every possible proxy config class name
        proxy_classes = [x for x in dir(proxy_module) if 'Proxy' in x or 'proxy' in x.lower()]
        print(f'Available proxy classes: {proxy_classes}')

        ytt_api = None
        for cls_name in proxy_classes:
            try:
                cls = getattr(proxy_module, cls_name)
                # Try different init signatures
                for kwargs in [
                    {'http_url': proxy_url, 'https_url': proxy_url},
                    {'proxy_url': proxy_url},
                    {'proxies': {'http': proxy_url, 'https': proxy_url}},
                ]:
                    try:
                        proxy_config = cls(**kwargs)
                        ytt_api = YouTubeTranscriptApi(proxy_config=proxy_config)
                        print(f'Success with {cls_name} and {list(kwargs.keys())}')
                        break
                    except Exception:
                        continue
                if ytt_api:
                    break
            except Exception:
                continue

        if not ytt_api:
            print('No proxy worked, trying direct')
            ytt_api = YouTubeTranscriptApi()

        fetched = ytt_api.fetch(video_id)
        transcript = ' '.join([snippet.text for snippet in fetched])
        print(f'Transcript: {len(transcript)} chars')

        if len(transcript) > 50:
            jobs[job_id] = {'status': 'done', 'transcript': transcript}
        else:
            jobs[job_id] = {'status': 'error', 'error': 'Transcript too short'}

    except Exception as e:
        print(f'Error: {e}')
        jobs[job_id] = {'status': 'error', 'error': str(e)}

@app.route('/factcheck', methods=['POST'])
def factcheck():
    try:
        import anthropic
        data = request.json
        text = data.get('text', '')[:1500]
        if not text:
            return jsonify({'error': 'No text'}), 400

        ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
        if not client:
            return jsonify({'error': 'No Anthropic key'}), 500

        prompt = (
            'You are FactLens. Today is April 2026. Search the web about this text then output ONLY JSON. '
            'No words before { or after }. '
            'Text: "' + text + '" '
            '{"verdict":{"type":"misleading","emoji":"warning","label":"MISLEADING","summary":"15 words max"},'
            '"claims":[{"status":"warn","quote":"10 words max","explanation":"20 words max","confidence":80}],'
            '"aiGenerated":{"detected":false,"confidence":40}} '
            'type=true/false/misleading/unverified emoji=check/cross/warning/question ONE claim only.'
        )

        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=800,
            tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
            messages=[{'role': 'user', 'content': prompt}]
        )

        full_text = ''.join([b.text for b in response.content if hasattr(b, 'text')])
        stripped = full_text.replace('```json', '').replace('```', '').strip()
        start = stripped.find('{"verdict"')
        if start == -1:
            start = stripped.find('{')
        end = stripped.rfind('}')
        if start == -1 or end == -1:
            return jsonify({'error': 'No JSON'}), 500

        result = json.loads(stripped[start:end+1])
        emoji_map = {'check': '✅', 'cross': '🚫', 'warning': '⚠️', 'question': '🔎'}
        if result.get('verdict'):
            result['verdict']['emoji'] = emoji_map.get(result['verdict']['emoji'], '🔎')

        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
