from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
import os
import subprocess
import json
import re
import tempfile
import requests
import time

app = Flask(__name__)
CORS(app)

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ASSEMBLYAI_API_KEY = os.environ.get('ASSEMBLYAI_API_KEY', '')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'FactLens Backend'})

@app.route('/transcribe', methods=['POST'])
def transcribe():
    try:
        data = request.json
        video_url = data.get('url', '')

        if not video_url:
            return jsonify({'error': 'No video URL provided'}), 400

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, 'audio.mp3')

            result = subprocess.run([
                'yt-dlp',
                '--extract-audio',
                '--audio-format', 'mp3',
                '--audio-quality', '5',
                '--output', audio_path,
                '--no-playlist',
                '--socket-timeout', '30',
                '--max-filesize', '10m',
                video_url
            ], capture_output=True, text=True, timeout=60)

            actual_path = audio_path
            if not os.path.exists(actual_path):
                for f in os.listdir(tmpdir):
                    if f.endswith(('.mp3', '.m4a', '.webm')):
                        actual_path = os.path.join(tmpdir, f)
                        break

            if not os.path.exists(actual_path):
                return jsonify({'error': 'Could not download video audio'}), 400

            if not ASSEMBLYAI_API_KEY:
                return jsonify({'error': 'AssemblyAI API key not configured'}), 500

            with open(actual_path, 'rb') as f:
                upload_response = requests.post(
                    'https://api.assemblyai.com/v2/upload',
                    headers={'authorization': ASSEMBLYAI_API_KEY},
                    data=f
                )
            upload_url = upload_response.json()['upload_url']

            transcript_response = requests.post(
                'https://api.assemblyai.com/v2/transcript',
                headers={'authorization': ASSEMBLYAI_API_KEY, 'content-type': 'application/json'},
                json={'audio_url': upload_url, 'language_code': 'en'}
            )
            transcript_id = transcript_response.json()['id']

            for _ in range(30):
                time.sleep(3)
                poll = requests.get(
                    f'https://api.assemblyai.com/v2/transcript/{transcript_id}',
                    headers={'authorization': ASSEMBLYAI_API_KEY}
                ).json()
                if poll['status'] == 'completed':
                    return jsonify({'transcript': poll['text']})
                elif poll['status'] == 'error':
                    return jsonify({'error': 'Transcription failed'}), 500

            return jsonify({'error': 'Transcription timed out'}), 408

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Video download timed out'}), 408
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/factcheck', methods=['POST'])
def factcheck():
    try:
        data = request.json
        text = data.get('text', '')

        if not text:
            return jsonify({'error': 'No text provided'}), 400

        clean = text[:1000]
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        prompt = 'You are FactLens, an AI fact-checker. Analyse this text and return ONLY valid JSON.\n\nText: "' + clean + '"\n\nReturn exactly this JSON:\n{"verdict":{"type":"false","emoji":"cross","label":"FALSE","summary":"one sentence verdict"},"claims":[{"status":"bad","quote":"specific claim","explanation":"two sentence explanation.","confidence":85}],"aiGenerated":{"detected":false,"confidence":50}}\n\nVERDICT RULES:\n- true: claim is accurate and supported by evidence\n- false: claim is demonstrably wrong with clear evidence\n- misleading: contains truth but framed deceptively or omits important context\n- unverified: cannot confirm or deny, specific figures not verifiable\n\nDO NOT use misleading just because a number cannot be verified. Use unverified instead.\nemoji: check/cross/warning/question. status: ok/bad/warn/info. Max 2 claims. Return ONLY the JSON.'

        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=600,
            tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
            messages=[{'role': 'user', 'content': prompt}]
        )

        full_text = ''
        for block in response.content:
            if hasattr(block, 'text'):
                full_text += block.text

        match = re.search(r'\{[\s\S]*\}', full_text)
        if not match:
            return jsonify({'error': 'No JSON in response'}), 500

        result = json.loads(match.group(0))

        emoji_map = {'check': '✅', 'cross': '🚫', 'warning': '⚠️', 'question': '🔎'}
        if result.get('verdict'):
            result['verdict']['emoji'] = emoji_map.get(result['verdict']['emoji'], '🔎')

        return jsonify({'success': True, 'data': result})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
